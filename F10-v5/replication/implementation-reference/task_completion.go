package orion

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const taskCompleteToolName = "task_complete"

// TaskCompletion is the terminal contract between the model, the execution
// harness, and UI clients. A turn is complete only because this contract was
// accepted, never because prose was returned or an arbitrary number of
// model/tool steps elapsed.
type TaskCompletion struct {
	Status string `json:"status"`
	Reason string `json:"reason"`
	// ExecutionResult classifies controller/infrastructure termination
	// independently from the model's semantic status.
	ExecutionResult  string                    `json:"executionResult,omitempty"`
	Outcome          string                    `json:"outcome,omitempty"`
	Summary          string                    `json:"summary"`
	Response         string                    `json:"response,omitempty"`
	Clarification    *TaskClarification        `json:"clarification,omitempty"`
	Refs             []string                  `json:"refs,omitempty"`
	EvidenceRefs     []string                  `json:"evidence_refs,omitempty"`
	Criteria         []TaskCompletionCriterion `json:"criteria"`
	ChangedFiles     []string                  `json:"changedFiles,omitempty"`
	Verification     []string                  `json:"verification,omitempty"`
	Unverified       []string                  `json:"unverifiedBoundaries,omitempty"`
	GitActions       []string                  `json:"gitActions,omitempty"`
	Artifacts        []TaskCompletionArtifact  `json:"artifacts,omitempty"`
	Blockers         []string                  `json:"blockers,omitempty"`
	RemainingActions []string                  `json:"remainingActions,omitempty"`
	Recommendations  []string                  `json:"recommendations,omitempty"`
	Actions          []TaskCompletionAction    `json:"actions,omitempty"`
	CheckpointID     string                    `json:"checkpointId,omitempty"`
	Explicit         bool                      `json:"-"`
	// ModelSummary preserves the accepted wire value when finalization replaces
	// a machine-style summary with human-readable control-plane text.
	ModelSummary string `json:"-"`
	// CompletionContract is host-owned execution metadata. It is never
	// serialized into the terminal receipt or exposed to the model.
	CompletionContract string `json:"-"`
}

type TaskCompletionCriterion struct {
	ID           string   `json:"id,omitempty"`
	Title        string   `json:"title"`
	State        string   `json:"state"`
	Evidence     string   `json:"evidence"`
	EvidenceRefs []string `json:"evidence_refs,omitempty"`
}

type TaskCompletionArtifact struct {
	Kind  string `json:"kind,omitempty"`
	Path  string `json:"path,omitempty"`
	URL   string `json:"url,omitempty"`
	Title string `json:"title,omitempty"`
}

type TaskCompletionAction struct {
	ID      string `json:"id"`
	Label   string `json:"label"`
	Kind    string `json:"kind"`
	Prompt  string `json:"prompt,omitempty"`
	Primary bool   `json:"primary,omitempty"`
}

func taskCompleteToolDefinition() ToolDefinition {
	return taskCompleteToolDefinitionForResponseMode("")
}

func taskCompleteToolDefinitionForConfig(cfg Config) ToolDefinition {
	return taskCompleteToolDefinitionForContract(cfg.ResponseMode, cfg.CompletionOutcomes, cfg.CompletionContract)
}

func taskCompleteToolDefinitionForResponseMode(responseMode string) ToolDefinition {
	return taskCompleteToolDefinitionForContract(responseMode, nil, "rich")
}

func taskCompleteToolDefinitionForContract(responseMode string, completionOutcomes []string, completionContract string) ToolDefinition {
	if normalizeCompletionContract(completionContract) == "qa-direct" {
		return ToolDefinition{
			Type: "function",
			Function: FunctionDef{
				Name:        taskCompleteToolName,
				Description: "Return the exact factual answer. Orion records response byte-for-byte and supplies the control-plane summary, delivery criterion, and host receipt. completed means an answer was delivered, not that an external evaluator marked it correct. Never upgrade blocked or partial to completed.",
				Parameters: objectSchema(map[string]any{
					"status":   map[string]any{"type": "string", "enum": []string{"completed", "partial", "blocked"}},
					"response": stringSchema("Exact user-facing factual answer. Preserve spelling, punctuation, numbers, versions, dates, names, units, and whitespace; Orion performs no semantic repair."),
				}, []string{"status", "response"}),
			},
		}
	}
	criterion := map[string]any{
		"type": "object",
		"properties": map[string]any{
			"id":       stringSchema("Stable short criterion identifier."),
			"title":    stringSchema("Concrete acceptance criterion."),
			"state":    map[string]any{"type": "string", "enum": []string{"met", "unmet"}},
			"evidence": stringSchema("Short human-readable explanation. This text is not trusted as proof."),
			"evidence_refs": map[string]any{
				"type":        "array",
				"items":       map[string]any{"type": "string"},
				"description": "Host-issued evidence IDs from successful tool results, for example ev_018. Do not invent IDs.",
			},
		},
		"required":             []string{"title", "state", "evidence", "evidence_refs"},
		"additionalProperties": false,
	}
	required := []string{"status", "summary", "criteria", "evidence_refs"}
	if normalizeResponseMode(responseMode) == "direct" {
		required = append(required, "response")
	}
	outcomeSchema := stringSchema("Caller-owned outcome code. Preserve the exact value required by the task adapter.")
	if allowed := cleanStringList(completionOutcomes); len(allowed) > 0 {
		outcomeSchema["enum"] = allowed
		required = append(required, "outcome")
	}
	criteriaSchema := map[string]any{
		"type":        "array",
		"items":       criterion,
		"description": "Acceptance criteria and evidence.",
	}
	if normalizeCompletionContract(completionContract) == "compact" {
		criteriaSchema = stringSchema("Concrete evidence that supports the terminal result. Cite the tool result or artifact used.")
	}
	return ToolDefinition{
		Type: "function",
		Function: FunctionDef{
			Name:        taskCompleteToolName,
			Description: "Set the terminal task outcome. Call this only when the requested outcome is complete, genuinely blocked, or intentionally partial. Step count and task_plan do not finish a task. completed requires every criterion met and grounded in host-issued evidence_refs; never invent an evidence ID. partial/blocked requires blockers or remaining_actions. In direct response mode, response carries the exact user-facing answer while summary remains a concise control-plane description. Put the actual requested content in response (query rows, issue body, search hits); never substitute a profile_id, evidence UUID, or 'link to the result'.",
			Parameters: objectSchema(map[string]any{
				"status":                map[string]any{"type": "string", "enum": []string{"completed", "partial", "blocked"}},
				"reason":                stringSchema("Short machine-readable reason, e.g. criteria_met, blocked, stalled, provider_failed."),
				"outcome":               outcomeSchema,
				"summary":               stringSchema("Concise outcome in the user's language."),
				"response":              stringSchema("Exact user-facing answer or requested payload. For SQL this is the result rows as a markdown table, not a profile_id or evidence UUID. Never replace the payload with a link to the result."),
				"refs":                  map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "Optional http(s) URLs or workspace file paths. Do not put profile_id, evidence IDs, or opaque UUIDs here."},
				"evidence_refs":         map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "Host-issued evidence IDs selected for the terminal result. Orion derives refs from these receipts."},
				"criteria":              criteriaSchema,
				"changed_files":         map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"verification":          map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"unverified_boundaries": map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"git_actions":           map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"artifacts":             map[string]any{"type": "array", "items": map[string]any{"type": "object", "properties": map[string]any{"kind": stringSchema("file, report, url, issue, or pull_request"), "path": stringSchema("Repository-relative file path."), "url": stringSchema("External URL."), "title": stringSchema("Display title.")}, "additionalProperties": false}},
				"blockers":              map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"remaining_actions":     map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
				"recommendations":       map[string]any{"type": "array", "items": map[string]any{"type": "string"}},
			}, required),
		},
	}
}

func normalizeCompletionContract(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "qa-direct", "qa_direct", "qadirect":
		return "qa-direct"
	case "compact":
		return "compact"
	default:
		return "rich"
	}
}

func appendTaskCompleteTool(defs []ToolDefinition) []ToolDefinition {
	return appendTaskCompleteToolForConfig(defs, Config{})
}

func appendTaskCompleteToolForConfig(defs []ToolDefinition, cfg Config) []ToolDefinition {
	// A clarification is a control-plane pause, not an executable capability.
	// Exact experimental surfaces and explicit deny lists remain authoritative.
	if !cfg.ExactToolSurface && !containsString(cfg.DeniedTools, taskCompleteToolName) &&
		!containsString(cfg.DeniedTools, requestUserInputToolName) && !hasToolDefinition(defs, requestUserInputToolName) {
		defs = append(defs, requestUserInputTool())
	}
	// An explicit deny-list is authoritative.  This is required by bounded
	// experimental runtimes whose model-facing surface must contain no implicit
	// control-plane tools.
	if containsString(cfg.DeniedTools, taskCompleteToolName) {
		return defs
	}
	for _, def := range defs {
		if def.Function.Name == taskCompleteToolName {
			return defs
		}
	}
	return append(defs, taskCompleteToolDefinitionForConfig(cfg))
}

func parseTaskCompletion(raw string) (*TaskCompletion, error) {
	return parseTaskCompletionWithContract(raw, "rich")
}

func decodeTaskCompletionWithContract(raw, completionContract string) (*TaskCompletion, error) {
	var wire struct {
		Status             string                   `json:"status"`
		Reason             string                   `json:"reason"`
		Outcome            string                   `json:"outcome"`
		Summary            string                   `json:"summary"`
		Response           string                   `json:"response"`
		Refs               any                      `json:"refs"`
		EvidenceRefs       any                      `json:"evidence_refs"`
		Criteria           any                      `json:"criteria"`
		AcceptanceCriteria any                      `json:"acceptance_criteria"`
		ChangedFiles       any                      `json:"changed_files"`
		Verification       any                      `json:"verification"`
		Unverified         any                      `json:"unverified_boundaries"`
		GitActions         any                      `json:"git_actions"`
		Artifacts          []TaskCompletionArtifact `json:"artifacts"`
		Blockers           any                      `json:"blockers"`
		RemainingActions   any                      `json:"remaining_actions"`
		Recommendations    any                      `json:"recommendations"`
	}
	if err := jsonUnmarshalLoose(raw, &wire); err != nil {
		return nil, err
	}
	status := normalizeCompletionStatus(wire.Status)
	criteria := compatibleTaskCompletionCriteria(wire.Criteria, status)
	if len(criteria) == 0 {
		criteria = compatibleTaskCompletionCriteria(wire.AcceptanceCriteria, status)
	}
	contract := normalizeCompletionContract(completionContract)
	summary := strings.TrimSpace(wire.Summary)
	response := strings.TrimSpace(wire.Response)
	if contract == "qa-direct" {
		// The answer is an evaluator-owned payload. Preserve the decoded JSON
		// string byte-for-byte; validation may inspect TrimSpace but must never
		// normalize its content.
		response = wire.Response
	}
	completion := &TaskCompletion{
		Status:             status,
		Reason:             strings.TrimSpace(wire.Reason),
		Outcome:            strings.TrimSpace(wire.Outcome),
		Summary:            summary,
		ModelSummary:       summary,
		Response:           response,
		Refs:               compatibleStringList(wire.Refs, 40),
		EvidenceRefs:       compatibleStringList(wire.EvidenceRefs, 64),
		Criteria:           criteria,
		ChangedFiles:       compatibleStringList(wire.ChangedFiles, 40),
		Verification:       compatibleStringList(wire.Verification, 20),
		Unverified:         compatibleStringList(wire.Unverified, 20),
		GitActions:         compatibleStringList(wire.GitActions, 20),
		Artifacts:          wire.Artifacts,
		Blockers:           compatibleStringList(wire.Blockers, 12),
		RemainingActions:   compatibleStringList(wire.RemainingActions, 12),
		Recommendations:    compatibleStringList(wire.Recommendations, 12),
		Explicit:           true,
		CompletionContract: contract,
	}
	if contract == "qa-direct" {
		if strings.TrimSpace(completion.Response) == "" {
			return nil, fmt.Errorf("task_complete qa-direct requires a non-empty response")
		}
		// The qa-direct wire contract contains only status and response. The
		// loose compatibility decoder may see extra keys from a provider text
		// relay, but none of those model-provided control-plane fields are
		// allowed to cross this boundary.
		completion.Reason = defaultCompletionReason(completion.Status)
		completion.Outcome = ""
		completion.Summary = completion.Response
		completion.Refs = nil
		completion.EvidenceRefs = nil
		completion.ChangedFiles = nil
		completion.Verification = nil
		completion.Unverified = nil
		completion.GitActions = nil
		completion.Artifacts = nil
		completion.Blockers = nil
		completion.RemainingActions = nil
		completion.Recommendations = nil
		state := "met"
		if completion.Status != "completed" {
			state = "unmet"
			completion.Blockers = []string{completion.Response}
		}
		completion.Criteria = []TaskCompletionCriterion{{
			ID:       "answer_delivered",
			Title:    "Answer delivered",
			State:    state,
			Evidence: "The host received task_complete.response unchanged.",
		}}
	}
	if contract == "compact" && len(completion.EvidenceRefs) > 0 {
		// Compact schemas expose criteria as a string, so per-criterion receipt
		// fields cannot be emitted. Bind the explicit top-level host receipts to
		// compact criteria before provenance validation. Rich contracts still
		// have to cite every criterion independently.
		for index := range completion.Criteria {
			if len(completion.Criteria[index].EvidenceRefs) == 0 {
				completion.Criteria[index].EvidenceRefs = append([]string(nil), completion.EvidenceRefs...)
			}
		}
	}
	if len(completion.Criteria) == 0 && contract == "compact" {
		state := "met"
		if completion.Status != "completed" {
			state = "unmet"
		}
		evidence := completion.Summary
		if len(completion.Refs) > 0 {
			evidence += " Refs: " + strings.Join(completion.Refs, ", ")
		}
		completion.Criteria = []TaskCompletionCriterion{{
			Title:        "Requested outcome delivered",
			State:        state,
			Evidence:     evidence,
			EvidenceRefs: append([]string(nil), completion.EvidenceRefs...),
		}}
	}
	if completion.Status != "completed" &&
		len(completion.Blockers) == 0 &&
		len(completion.RemainingActions) == 0 &&
		completion.Summary != "" {
		// Providers occasionally put the complete blocking reason in summary
		// and omit the blocker arrays, even when they accepted the rich schema.
		// Preserve that model-provided reason as the canonical blocker instead
		// of burning the terminal-repair budget on an otherwise honest
		// partial/blocked result. This never upgrades the reported status.
		completion.Blockers = []string{completion.Summary}
	}
	if contract == "compact" &&
		completion.Response == "" &&
		completion.Summary != "" {
		// Strict external adapters require a non-empty user-facing payload even
		// for honest partial/blocked outcomes. Reuse the model's own summary;
		// never synthesize an answer or upgrade the outcome.
		completion.Response = completion.Summary
	}
	return completion, nil
}

// parseTaskCompletionWithContract decodes a raw task_complete payload and then
// enforces the terminal contract. Acceptance paths that must reconcile the
// payload against host-owned runtime evidence (for example, immutable policy
// denials recorded while blocking a mutation) decode first, reconcile, and only
// then validate; every other caller keeps decode+validate atomic here.
func parseTaskCompletionWithContract(raw, completionContract string) (*TaskCompletion, error) {
	completion, err := decodeTaskCompletionWithContract(raw, completionContract)
	if err != nil {
		return nil, err
	}
	return finalizeParsedTaskCompletion(completion)
}

// finalizeParsedTaskCompletion applies the terminal-contract validation and the
// canonical defaults shared by every task_complete payload after decoding.
func finalizeParsedTaskCompletion(completion *TaskCompletion) (*TaskCompletion, error) {
	if err := validateTaskCompletion(completion); err != nil {
		return nil, err
	}
	completion.Reason = firstNonEmptyString(completion.Reason, defaultCompletionReason(completion.Status))
	completion.Actions = completionActions(completion)
	return completion, nil
}

// compatibleTaskCompletionCriteria accepts the common acceptance_criteria
// spelling used by OpenAI-compatible providers while preserving Orion's
// evidence-bearing terminal contract. It does not invent success: the caller
// must still provide non-empty evidence, and every canonical validation below
// remains in force.
func compatibleTaskCompletionCriteria(raw any, status string) []TaskCompletionCriterion {
	state := "met"
	if status != "completed" {
		state = "unmet"
	}
	fromText := func(value string) []TaskCompletionCriterion {
		value = strings.TrimSpace(value)
		if value == "" {
			return nil
		}
		return []TaskCompletionCriterion{{
			Title:    "Requested outcome delivered",
			State:    state,
			Evidence: value,
		}}
	}

	switch value := raw.(type) {
	case string:
		return fromText(value)
	case []any:
		var out []TaskCompletionCriterion
		for _, item := range value {
			if text, ok := item.(string); ok {
				out = append(out, fromText(text)...)
				continue
			}
			if object, ok := item.(map[string]any); ok {
				out = append(out, compatibleTaskCompletionCriterion(object, state)...)
			}
		}
		return out
	case map[string]any:
		return compatibleTaskCompletionCriterion(value, state)
	default:
		return nil
	}
}

func compatibleTaskCompletionCriterion(raw map[string]any, fallbackState string) []TaskCompletionCriterion {
	title := strings.TrimSpace(fmt.Sprint(raw["title"]))
	evidence := strings.TrimSpace(fmt.Sprint(raw["evidence"]))
	evidenceRefs := compatibleStringList(raw["evidence_refs"], 32)
	state := strings.ToLower(strings.TrimSpace(fmt.Sprint(raw["state"])))
	if state == "" {
		state = fallbackState
	}
	if title == "" && evidence != "" {
		title = "Requested outcome delivered"
	}
	if title == "" && evidence == "" {
		return nil
	}
	return []TaskCompletionCriterion{{
		Title:        title,
		State:        state,
		Evidence:     evidence,
		EvidenceRefs: evidenceRefs,
	}}
}

func acceptTaskCompletionForRun(raw string, runner *ToolRunner, quality *QualityState, progress agentRunProgress, sessionID, originalPrompt string) (*TaskCompletion, string) {
	return acceptTaskCompletionForRunMutable(raw, runner, quality, &progress, sessionID, originalPrompt)
}

func acceptTaskCompletionForRunMutable(raw string, runner *ToolRunner, quality *QualityState, progress *agentRunProgress, sessionID, originalPrompt string) (*TaskCompletion, string) {
	completionContract := "rich"
	if runner != nil {
		completionContract = runner.completionContract
	}
	if progress == nil {
		progress = &agentRunProgress{}
	}
	completion, err := decodeTaskCompletionWithContract(raw, completionContract)
	if err != nil {
		return nil, toolError(err)
	}
	// A permanently denied out-of-scope mutation is host-verified evidence, not
	// unfinished work. Reconcile it out of the model's self-authored contract
	// before validation so an otherwise complete primary task is not forced into
	// terminal_contract_failed or an accepted-but-non-terminal partial.
	reconcileCompletionWithAbsorbedPolicyDenials(completion, progress)
	completion, err = finalizeParsedTaskCompletion(completion)
	if err != nil {
		return nil, toolError(err)
	}
	if completionContract == "qa-direct" {
		if runner != nil && len(runner.completionOutcomes) == 1 {
			completion.Outcome = runner.completionOutcomes[0]
		}
		if receipt := progress.RecordResponseReceipt(sessionID, completion.Response); receipt != nil {
			completion.EvidenceRefs = []string{receipt.ID}
			for index := range completion.Criteria {
				completion.Criteria[index].EvidenceRefs = []string{receipt.ID}
			}
		}
	}
	if runner != nil && len(runner.completionOutcomes) > 0 {
		allowed := false
		for _, outcome := range runner.completionOutcomes {
			if completion.Outcome == outcome {
				allowed = true
				break
			}
		}
		if !allowed {
			return nil, toolError(fmt.Errorf(
				"completion_rejected: task_complete.outcome must be one of [%s], got %q",
				strings.Join(runner.completionOutcomes, ", "),
				completion.Outcome,
			))
		}
	}
	// An unavailable task-carried verifier is a host-proven terminal boundary.
	// Weak models frequently report completed after that result; rejecting the
	// proposal only makes them repeat the same task_complete call while no new
	// verifier can appear. Preserve the completed work, downgrade the outcome to
	// an honest partial/blocked result, and close the turn on the first proposal.
	if completion.Status == "completed" &&
		progressProvesNoVerifierBoundary(*progress) &&
		!taskTreatsFailedVerificationAsDiagnosticEvidence(originalPrompt) {
		completion = noVerifierBoundaryTaskCompletion(*progress)
	}
	if completion.Status == "completed" {
		if quality != nil {
			if err := quality.WorkspaceHygieneError(); err != nil {
				return nil, toolError(fmt.Errorf("completion_rejected: %w", err))
			}
		}
		var receiptErr error
		acceptedAuthoritativeReceipt := false
		if quality != nil {
			receiptErr = quality.ValidVerificationReceipt()
			acceptedAuthoritativeReceipt = receiptErr == nil
		}
		if unresolved := progress.unresolvedBlockingErrors(); len(unresolved) > 0 {
			return nil, toolError(fmt.Errorf("completion_rejected: unresolved required tool failure: %s", strings.Join(unresolved, "; ")))
		}
		if runner != nil {
			if guard := runner.finalPolicyGateMessage(); guard != "" {
				return nil, toolError(fmt.Errorf("completion_rejected: %s", guard))
			}
		}
		diagnosticVerification := taskTreatsFailedVerificationAsDiagnosticEvidence(originalPrompt)
		if diagnosticVerification && promptRequiresAutomatedTestRun(originalPrompt) &&
			(quality == nil || len(quality.DiagnosticVerificationResults) == 0) {
			return nil, toolError(fmt.Errorf("completion_rejected: the requested diagnostic test has not been executed; run the specified check and use its pass/fail result as evidence"))
		}
		if taskCompletionRequiresAutomatedTestRun(completion, originalPrompt) {
			if quality == nil {
				return nil, toolError(fmt.Errorf("completion_rejected: required automated test execution has no harness verification state; run the complete authoritative run_tests manifest before task_complete"))
			}
			if !acceptedAuthoritativeReceipt {
				detail := strings.TrimSpace(quality.VerificationDetail)
				if detail != "" {
					detail = "; last verification: " + trimForPrompt(firstLine(detail), 500)
				}
				return nil, toolError(fmt.Errorf("completion_rejected: required automated test execution has no accepted authoritative run_tests receipt: %v%s", receiptErr, detail))
			}
		}
		if acceptedAuthoritativeReceipt {
			// The receipt is the completion protocol's source of truth for the
			// current workspace. Historical or lower-scope diagnostics remain
			// in the lossless trace, but must not be promoted back into final
			// blockers after a stronger authoritative run has been accepted.
			updated := progress.withAcceptedAuthoritativeVerification()
			*progress = updated
			rebindStaleVerificationRefsToAcceptedReceipt(completion, *progress, sessionID)
		}
		if boundaries := progress.unverifiedBoundaryLines(false); len(boundaries) > 0 &&
			!taskTreatsFailedVerificationAsDiagnosticEvidence(originalPrompt) {
			return nil, toolError(fmt.Errorf("completion_rejected: a verifier still has an unverified boundary: %s", strings.Join(boundaries, "; ")))
		}
		// Only local workspace mutations require a build/test/static verifier.
		// Successful MCP mutations already have a harness-owned tool receipt and
		// are independently checked by the external system/benchmark grader. Treating
		// them as unverified local file writes traps tool agents in task_complete
		// loops even after the requested state transition succeeded.
		workspaceWrites := progress.FsWriteCount + progress.FsEditCount + progress.FsDeleteCount + progress.FsPatchCount
		enforceWorkspaceVerification := runner == nil || runner.verificationRequired
		currentIntent := classifyTaskIntent(originalPrompt, TaskSelection{})
		if enforceWorkspaceVerification && quality != nil && quality.WriteCount > workspaceWrites &&
			currentIntent.Kind == "code_change" {
			workspaceWrites = quality.WriteCount
		}
		if enforceWorkspaceVerification && workspaceWrites > 0 {
			verified := progress.hasPassedVerification() || (quality != nil && quality.VerificationOK)
			requiresTests := containsString(currentIntent.RequiredValidation, "tests_authoritative")
			codeChange := currentIntent.Kind == "code_change"
			// A follow-up markdown/MCP/question turn must not inherit the previous
			// code_change write count or its test obligation.
			if currentIntent.RequiresFileWrite || requiresTests {
				if codeChange && !verified {
					return nil, toolError(fmt.Errorf("completion_rejected: workspace changes require recorded verification evidence from a passed executable verifier; a verifier call or failed/unavailable result is not evidence of correctness"))
				}
				if !codeChange && currentIntent.Kind != "artifact_creation" && currentIntent.Kind != "data_artifact" && len(progress.Verifiers) == 0 && !verified {
					return nil, toolError(fmt.Errorf("completion_rejected: workspace changes require recorded verification evidence"))
				}
			}
		}
		if len(progress.ManualActions) > 0 {
			return nil, toolError(fmt.Errorf("completion_rejected: manual actions are still required; use partial or blocked"))
		}
	}
	if err := runCompletionValidators(completionValidationContext{
		Completion:     completion,
		Runner:         runner,
		Quality:        quality,
		Progress:       *progress,
		TaskID:         sessionID,
		OriginalPrompt: originalPrompt,
	}); err != nil {
		return nil, toolError(fmt.Errorf("completion_rejected: %w", err))
	}
	// task_plan is resumable model scratch state, not independent proof of task
	// incompleteness. Once substantive host validators accept the result, clear
	// stale plan metadata instead of requiring a ceremonial provider round trip.
	if completion.Status == "completed" && runner != nil {
		clearTaskPlanForSession(runner.home, runner.cwd, runner.sessionID)
	}
	completion = finalizeTaskCompletion(completion, *progress, sessionID, originalPrompt)
	return completion, "accepted: task_complete status=" + completion.Status
}

func progressProvesNoVerifierBoundary(progress agentRunProgress) bool {
	for _, evidence := range progress.VerificationEvidence {
		if evidence.Status != "unavailable" {
			continue
		}
		detail := strings.ToLower(strings.TrimSpace(evidence.Detail))
		if strings.Contains(detail, "environment_no_verifier") ||
			strings.Contains(detail, "no task-carried test runner") {
			return true
		}
	}
	return false
}

func noVerifierBoundaryTaskCompletion(progress agentRunProgress) *TaskCompletion {
	blocker := "environment_no_verifier: no task-carried verifier is available for the changed workspace"
	for index := len(progress.VerificationEvidence) - 1; index >= 0; index-- {
		evidence := progress.VerificationEvidence[index]
		if evidence.Status == "unavailable" && strings.TrimSpace(evidence.Detail) != "" {
			blocker = strings.TrimSpace(evidence.Detail)
			break
		}
	}
	completion := incompleteTaskCompletion(
		"The requested changes were made, but the environment has no task-carried verifier that can prove them correct.",
		"environment_no_verifier",
		blocker,
		progress,
	)
	completion.ExecutionResult = "PARTIAL"
	return completion
}

func taskTreatsFailedVerificationAsDiagnosticEvidence(prompt string) bool {
	prompt = capabilityTaskInstruction(prompt)
	if taskExplicitlyRequestsRepositoryChange(prompt) || taskExpectsIntegrationWrite(prompt) {
		return false
	}
	lower := strings.ToLower(prompt)
	return containsAny(lower, []string{
		"find the bug", "find a bug", "locate the bug", "diagnose", "root cause",
		"investigate the failure", "why does", "code review", "review the code",
		"найди баг", "найти баг", "найди ошибку", "найти ошибку", "диагност",
		"причин", "почему пада", "почему не работа", "ревью кода", "проведи ревью",
	}...)
}

var explicitOutputPathPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\b(?:write|save|store|export|record|put|place)\b.{0,100}?\b(?:to|as|at|into)\s+(?:the\s+)?(?:file\s+)?[[:space:]]*[` + "`" + `"'“”]?([./\\A-Za-z0-9_-]*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,16})`),
	regexp.MustCompile(`(?i)\b(?:output|answer|result|report)\s+(?:file|artifact)\s*(?:is|:|=)\s*[` + "`" + `"'“”]?([./\\A-Za-z0-9_-]*[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]{1,16})`),
	regexp.MustCompile(`(?i)\b(?:запиши|сохрани|помести|выгрузи)\b.{0,100}?\b(?:в|как)\s+(?:файл\s+)?[` + "`" + `"'«»]?([./\\A-Za-z0-9А-Яа-яЁё_-]*[A-Za-z0-9А-Яа-яЁё_.-]+\.[A-Za-zА-Яа-яЁё0-9_-]{1,16})`),
}

func explicitRequiredOutputPaths(prompt string) []string {
	var paths []string
	seen := map[string]bool{}
	for _, pattern := range explicitOutputPathPatterns {
		for _, match := range pattern.FindAllStringSubmatch(prompt, -1) {
			if len(match) < 2 {
				continue
			}
			path := strings.Trim(strings.TrimSpace(match[1]), "`\"'“”«».,;:")
			if path == "" {
				continue
			}
			key := strings.ToLower(filepath.ToSlash(filepath.Clean(path)))
			if seen[key] {
				continue
			}
			seen[key] = true
			paths = append(paths, path)
		}
	}
	return paths
}

func missingExplicitOutputArtifacts(prompt string, runner ToolRunner, progress agentRunProgress) []string {
	required := explicitRequiredOutputPaths(prompt)
	if len(required) == 0 {
		return nil
	}
	changed := map[string]bool{}
	for _, path := range append(append([]string(nil), progress.FilesChanged...), progress.ArtifactWrites...) {
		changed[normalizedArtifactPath(path, runner.workspacePathAliases)] = true
	}
	var missing []string
	for _, rawPath := range required {
		path := guardedAutoWorkspaceAlias(rawPath, runner.workspacePathAliases)
		normalized := normalizedArtifactPath(path, nil)
		if changed[normalized] {
			continue
		}
		target, err := runner.resolve(path)
		if err == nil {
			if info, statErr := os.Stat(target); statErr == nil && info.Mode().IsRegular() {
				continue
			}
		}
		missing = append(missing, rawPath)
	}
	return missing
}

func normalizedArtifactPath(path string, aliases []string) string {
	path = guardedAutoWorkspaceAlias(strings.TrimSpace(path), aliases)
	path = strings.Trim(path, "`\"'“”«».,;:")
	return strings.ToLower(normalizeReportedPath(path))
}

func validateExactOutputArtifacts(prompt string, completion *TaskCompletion, runner ToolRunner) error {
	if completion == nil || strings.TrimSpace(completion.Response) == "" || !promptRequiresExactAnswer(prompt) {
		return nil
	}
	expected := strings.TrimSpace(completion.Response)
	for _, rawPath := range explicitRequiredOutputPaths(prompt) {
		path := guardedAutoWorkspaceAlias(rawPath, runner.workspacePathAliases)
		target, err := runner.resolve(path)
		if err != nil {
			continue
		}
		info, statErr := os.Stat(target)
		if statErr != nil || !info.Mode().IsRegular() || info.Size() > 2<<20 {
			continue
		}
		data, readErr := os.ReadFile(target)
		if readErr != nil {
			continue
		}
		actual := exactArtifactPayload(data)
		if actual != "" && strings.TrimSpace(actual) != expected {
			return fmt.Errorf(
				"the exact-answer artifact %s does not match task_complete.response; remove prefixes, explanations, evidence, and status prose from the artifact's primary answer payload, then retry",
				rawPath,
			)
		}
	}
	return nil
}

func promptRequiresExactAnswer(prompt string) bool {
	lower := strings.ToLower(effectiveTaskInstruction(prompt))
	for _, marker := range []string{
		"return only", "answer only", "answer with the exact", "answer with exact",
		"respond with", "reply with", "exact value only", "exact name only",
		"только ответ", "ответь только", "верни только", "точное значение", "точное имя",
	} {
		if strings.Contains(lower, marker) {
			return true
		}
	}
	return false
}

func effectiveTaskInstruction(prompt string) string {
	normalized := strings.ReplaceAll(prompt, "\r\n", "\n")
	// A newly created Desktop sidecar receives a bounded transcript followed by
	// an authoritative current-task section. Historical assistant prose is
	// context, not permission or intent: letting it participate in routing can
	// turn "generate the project" back into the earlier read-only report task.
	latestIndex := -1
	latestEnd := 0
	for _, marker := range []string{
		"## Current user task\n",
		"\nUser:\n", "\nUSER:\n", "\nПользователь:\n",
	} {
		if index := strings.LastIndex(normalized, marker); index >= 0 && index > latestIndex {
			latestIndex = index
			latestEnd = index + len(marker)
		}
	}
	if latestIndex >= 0 {
		return strings.TrimSpace(normalized[latestEnd:])
	}
	return strings.TrimSpace(normalized)
}

func exactArtifactPayload(data []byte) string {
	raw := strings.TrimSpace(string(data))
	if raw == "" {
		return ""
	}
	var object map[string]any
	if err := jsonUnmarshalLoose(raw, &object); err != nil {
		return raw
	}
	hasEnvelopeMetadata := false
	for _, key := range []string{"status", "outcome", "refs", "artifacts"} {
		if _, ok := object[key]; ok {
			hasEnvelopeMetadata = true
			break
		}
	}
	if !hasEnvelopeMetadata {
		return raw
	}
	for _, key := range []string{"response", "answer", "result", "output", "message"} {
		if value, ok := object[key].(string); ok {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

// taskCompletionRequiresAutomatedTestRun distinguishes an acceptance criterion
// that requires executing a real test runner from static/read-only analysis.
// This intentionally does not treat every mention of tests as mandatory: a
// report about unavailable tests or an explicitly heuristic task can still be
// completed without inventing an execution result. Conversely, a model cannot
// hide a failed run_tests call by omitting the test criterion when the user
// explicitly required a run.
func taskCompletionRequiresAutomatedTestRun(completion *TaskCompletion, originalPrompt string) bool {
	if taskTreatsFailedVerificationAsDiagnosticEvidence(originalPrompt) {
		return false
	}
	if promptRequiresAutomatedTestRun(originalPrompt) {
		return true
	}
	if completion == nil {
		return false
	}
	for _, criterion := range completion.Criteria {
		if criterion.State == "met" && claimsSuccessfulAutomatedTestRun(criterion.Title+" "+criterion.Evidence) {
			return true
		}
	}
	for _, verification := range completion.Verification {
		if claimsSuccessfulAutomatedTestRun(verification) {
			return true
		}
	}
	return false
}

func promptForbidsAutomatedTestRun(prompt string) bool {
	lower := strings.ToLower(strings.TrimSpace(capabilityTaskInstruction(prompt)))
	if lower == "" {
		return false
	}
	return containsAny(lower,
		"not required", "do not run", "don't run", "without running", "no automated test run",
		"do not touch test", "don't touch test", "do not modify test", "don't modify test",
		"do not add test", "don't add test", "without adding test",
		"не требуется", "не требуются", "не нужно запускать", "не запускай", "без запуска",
		"не трогай тест", "не меняй тест", "тесты не трогай", "тесты не меняй",
		"не добавляй тест", "без добавления тест",
	)
}

func promptRequiresAutomatedTestRun(prompt string) bool {
	lower := strings.ToLower(strings.TrimSpace(capabilityTaskInstruction(prompt)))
	if lower == "" {
		return false
	}
	if promptForbidsAutomatedTestRun(prompt) {
		return false
	}
	for _, required := range []string{
		"run tests", "run the tests", "run all tests", "run full tests", "run the full tests",
		"run the complete test", "run the complete regression suite", "execute tests", "execute the tests",
		"run_tests",
		"test execution is an explicit acceptance criterion", "mvn test", "go test", "npm test",
		"gradle test", "cargo test", "pytest", "playwright test",
		"запусти тест", "запустить тест", "прогони тест", "выполни тест", "проверь тестами",
		"проверить тестами", "используй тест", "использовать тест", "используйте тест",
		"запуск тестов", "тесты должны пройти",
	} {
		if strings.Contains(lower, required) {
			return true
		}
	}
	if explicitVerifierCommand(prompt) != "" && containsAny(lower,
		"run ", "execute ", "launch ", "запусти", "запустить", "прогони", "выполни",
	) {
		return true
	}
	return false
}

func claimsSuccessfulAutomatedTestRun(text string) bool {
	lower := strings.ToLower(strings.TrimSpace(text))
	if lower == "" {
		return false
	}
	for _, negation := range []string{
		"not run", "not executed", "unavailable", "not required", "static", "heuristic",
		"не запуск", "не выполн", "недоступ", "не треб", "статичес", "эвристич",
	} {
		if strings.Contains(lower, negation) {
			return false
		}
	}
	for _, claim := range []string{
		"tests pass", "all tests pass", "tests passed", "test passed", "test suite passed", "regression suite passed",
		"tests executed", "tests completed", "test run passed", "mvn test passed", "go test ./... passed",
		"npm test passed", "gradle test passed", "cargo test passed", "pytest passed", "run_tests: pass", "run_tests pass",
		"тесты прошли", "тесты пройдены", "тесты выполнены", "тесты запущены",
		"тест прошёл", "тест пройден", "набор тестов прош", "прогон тестов успеш",
	} {
		if strings.Contains(lower, claim) {
			return true
		}
	}
	return false
}

func normalizeCompletionStatus(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "completed", "complete", "done", "success":
		return "completed"
	case "partial", "partially_completed", "partially completed":
		return "partial"
	case "blocked", "failed":
		return "blocked"
	default:
		return strings.ToLower(strings.TrimSpace(status))
	}
}

func validateTaskCompletion(completion *TaskCompletion) error {
	if completion == nil {
		return fmt.Errorf("task_complete requires an outcome")
	}
	if completion.Status != "completed" && completion.Status != "partial" && completion.Status != "blocked" {
		return fmt.Errorf("task_complete status must be completed, partial, or blocked")
	}
	if completion.Summary == "" {
		return fmt.Errorf("task_complete requires a non-empty summary")
	}
	if len(completion.Criteria) == 0 {
		return fmt.Errorf(`task_complete requires a non-empty criteria array, for example: {"criteria":[{"title":"Requested outcome delivered","state":"met","evidence":"specific tool result or artifact"}]}`)
	}
	allMet := true
	for index := range completion.Criteria {
		criterion := &completion.Criteria[index]
		criterion.ID = strings.TrimSpace(criterion.ID)
		criterion.Title = strings.TrimSpace(criterion.Title)
		criterion.State = strings.ToLower(strings.TrimSpace(criterion.State))
		criterion.Evidence = strings.TrimSpace(criterion.Evidence)
		if criterion.Title == "" || criterion.Evidence == "" {
			return fmt.Errorf("task_complete criterion %d requires title and evidence", index+1)
		}
		if criterion.State != "met" && criterion.State != "unmet" {
			return fmt.Errorf("task_complete criterion %d state must be met or unmet", index+1)
		}
		if criterion.State != "met" {
			allMet = false
		}
	}
	if completion.Status == "completed" {
		if !allMet {
			return fmt.Errorf("task_complete cannot report completed while a criterion is unmet")
		}
		if len(completion.Blockers) > 0 || len(completion.RemainingActions) > 0 {
			return fmt.Errorf("task_complete cannot report completed with blockers or remaining_actions")
		}
	} else if len(completion.Blockers) == 0 && len(completion.RemainingActions) == 0 {
		return fmt.Errorf("task_complete %s requires a blocker or remaining action", completion.Status)
	}
	return nil
}

func stalledTaskCompletion(summary, blocker string, progress agentRunProgress) *TaskCompletion {
	completion := incompleteTaskCompletion(summary, "stalled", blocker, progress)
	completion.Explicit = true
	return completion
}

func incompleteTaskCompletion(summary, reason, blocker string, progress agentRunProgress) *TaskCompletion {
	status := "blocked"
	if len(progress.SuccessfulTools) > 0 || len(progress.FilesChanged) > 0 || len(progress.ExternalActions) > 0 || progress.hasPassedVerification() {
		status = "partial"
	}
	blockers := cleanCompletionStrings(progress.unresolvedBlockingErrors(), 12)
	if len(blockers) == 0 && (!progress.hasMaterialEvidence() || !progress.hasUnverifiedBoundary()) {
		blockers = cleanCompletionStrings([]string{strings.TrimSpace(blocker)}, 12)
	}
	if len(blockers) == 0 && status == "blocked" {
		blockers = []string{"The requested outcome is not complete."}
	}
	remaining := cleanCompletionStrings(progress.ManualActions, 12)
	if len(remaining) == 0 {
		if progress.hasUnverifiedBoundary() {
			remaining = []string{"Rerun only the unverified complete test suite from the saved checkpoint; do not repeat the successful isolated test or investigation."}
		} else {
			remaining = []string{"Continue from the saved context and resolve the blocker without restarting discovery."}
		}
	}
	criteria := inferredCompletionCriteria(progress, false)
	if len(criteria) == 0 {
		criteria = []TaskCompletionCriterion{{
			ID:       "requested_outcome",
			Title:    "Requested outcome delivered",
			State:    "unmet",
			Evidence: strings.Join(blockers, "; "),
		}}
	}
	completion := &TaskCompletion{
		Status:           status,
		Reason:           firstNonEmptyString(strings.TrimSpace(reason), "blocked"),
		Summary:          strings.TrimSpace(summary),
		Criteria:         criteria,
		Blockers:         blockers,
		RemainingActions: remaining,
		Explicit:         true,
	}
	completion.ChangedFiles = append(completion.ChangedFiles, progress.FilesChanged...)
	completion.Verification = append(completion.Verification, progress.Verifiers...)
	completion.Actions = completionActions(completion)
	return completion
}

func finalizeTaskCompletion(completion *TaskCompletion, progress agentRunProgress, sessionID string, originalPrompt ...string) *TaskCompletion {
	if completion == nil {
		return nil
	}
	copyValue := *completion
	russian := len(originalPrompt) > 0 && outcomeInRussian(originalPrompt[0])
	copyValue.ChangedFiles = normalizeChangedFileList(mergeCompletionStrings(progress.FilesChanged, copyValue.ChangedFiles, 40))
	copyValue.Verification = mergeCompletionStrings(progress.passedVerificationLines(russian), filterReportedVerification(copyValue.Verification, progress), 20)
	copyValue.Unverified = mergeCompletionStrings(progress.unverifiedBoundaryLines(russian), copyValue.Unverified, 20)
	copyValue.GitActions = mergeCompletionStrings(progress.GitActions, copyValue.GitActions, 20)
	copyValue.Blockers = filterCompletionBlockers(copyValue.Blockers, progress)
	copyValue.Blockers = mergeCompletionStrings(copyValue.Blockers, progress.unresolvedBlockingErrors(), 12)
	if progress.hasMaterialEvidence() {
		copyValue.Criteria = removeGenericIncompleteCriteria(copyValue.Criteria)
		copyValue.Criteria = mergeCompletionCriteria(inferredCompletionCriteria(progress, russian), copyValue.Criteria)
		if copyValue.Status == "blocked" {
			copyValue.Status = "partial"
		}
		if len(copyValue.Unverified) > 0 && len(copyValue.RemainingActions) == 0 {
			copyValue.RemainingActions = []string{localOutcomeText(russian,
				"Повторить только полный набор тестов из сохранённого состояния; уже прошедший изолированный тест и исследование не повторять.",
				"Rerun only the complete test suite from the saved checkpoint; do not repeat the successful isolated test or investigation.")}
		}
		copyValue.Summary = evidenceAwareSummary(copyValue.Summary, progress, russian)
	}
	if russian {
		copyValue = localizeKnownCompletionBoilerplate(copyValue)
	}
	for _, path := range progress.ReportArtifacts {
		copyValue.Artifacts = append(copyValue.Artifacts, TaskCompletionArtifact{Kind: "report", Path: path, Title: path})
	}
	for _, action := range progress.ExternalActions {
		if strings.TrimSpace(action.URL) == "" {
			continue
		}
		kind := "url"
		switch action.Tool {
		case "jira_create_issue":
			kind = "issue"
		case "github_pr_create", "gitlab_mr_create":
			kind = "pull_request"
		}
		copyValue.Artifacts = append(copyValue.Artifacts, TaskCompletionArtifact{
			Kind:  kind,
			URL:   strings.TrimSpace(action.URL),
			Title: strings.TrimSpace(action.Description),
		})
	}
	copyValue.Artifacts = cleanCompletionArtifacts(copyValue.Artifacts)
	if copyValue.CheckpointID == "" {
		copyValue.CheckpointID = fmt.Sprintf("%s-%d", strings.TrimSpace(sessionID), time.Now().UTC().UnixMilli())
	}
	hydrateFinalCompletionEvidence(&copyValue, progress)
	hydrateUserFacingAnswer(&copyValue, progress)
	humanizeMachineCompletionSummary(&copyValue, russian)
	copyValue.Actions = completionActions(&copyValue)
	if isUserInputCompletion(&copyValue) {
		copyValue.Actions = nil
		copyValue.Response = copyValue.Clarification.Question
	} else if copyValue.ExecutionResult == "STALLED" {
		// A stopped semantic loop needs changed input/source/strategy, not an
		// automatic Continue button that immediately replays the same failure.
		copyValue.Actions = nil
	}
	return &copyValue
}

var machineCompletionSummaryPattern = regexp.MustCompile(`^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$`)

// humanizeMachineCompletionSummary preserves benchmark/control-plane sentinels
// in Outcome while keeping the user-facing summary useful.
func humanizeMachineCompletionSummary(completion *TaskCompletion, russian bool) {
	if completion == nil {
		return
	}
	// qa-direct response is evaluator-owned data. In particular, a response
	// that resembles an outcome sentinel must not be promoted into Outcome or
	// rewritten as a humanized control-plane summary.
	if normalizeCompletionContract(completion.CompletionContract) == "qa-direct" {
		return
	}
	summary := strings.TrimSpace(completion.Summary)
	if !machineCompletionSummaryPattern.MatchString(summary) {
		return
	}
	if strings.TrimSpace(completion.Outcome) == "" {
		completion.Outcome = summary
	}
	parts := make([]string, 0, 4)
	if len(completion.ChangedFiles) > 0 {
		parts = append(parts, localOutcomeText(russian, fmt.Sprintf("изменено файлов: %d", len(completion.ChangedFiles)), fmt.Sprintf("files changed: %d", len(completion.ChangedFiles))))
	}
	if len(completion.Artifacts) > 0 {
		parts = append(parts, localOutcomeText(russian, fmt.Sprintf("подготовлено артефактов: %d", len(completion.Artifacts)), fmt.Sprintf("artifacts prepared: %d", len(completion.Artifacts))))
	}
	if len(completion.Verification) > 0 {
		parts = append(parts, localOutcomeText(russian, fmt.Sprintf("проверок зафиксировано: %d", len(completion.Verification)), fmt.Sprintf("verification checks recorded: %d", len(completion.Verification))))
	}
	met := 0
	for _, criterion := range completion.Criteria {
		if strings.EqualFold(strings.TrimSpace(criterion.State), "met") {
			met++
		}
	}
	if met > 0 {
		parts = append(parts, localOutcomeText(russian, fmt.Sprintf("критериев выполнено: %d из %d", met, len(completion.Criteria)), fmt.Sprintf("criteria met: %d of %d", met, len(completion.Criteria))))
	}
	lead := localOutcomeText(russian, "Задача выполнена", "The task was completed")
	if completion.Status != "completed" {
		lead = localOutcomeText(russian, "Задача завершена не полностью", "The task was not fully completed")
	}
	if len(parts) == 0 {
		completion.Summary = lead + "."
		return
	}
	completion.Summary = lead + ": " + strings.Join(parts, "; ") + "."
}

func inferredCompletionCriteria(progress agentRunProgress, russian bool) []TaskCompletionCriterion {
	var criteria []TaskCompletionCriterion
	files := normalizeChangedFileList(progress.FilesChanged)
	if len(files) > 0 {
		criteria = append(criteria, TaskCompletionCriterion{
			ID:       "implementation",
			Title:    localOutcomeText(russian, "Исправление реализовано", "Requested fix implemented"),
			State:    "met",
			Evidence: strings.Join(files, ", "),
		})
	}
	if len(progress.AbsorbedPolicyDenials) > 0 {
		criteria = append(criteria, TaskCompletionCriterion{
			ID:       "safety_policy_enforced",
			Title:    localOutcomeText(russian, "Неизменяемая граница безопасности соблюдена", "Immutable safety boundary preserved"),
			State:    "met",
			Evidence: strings.Join(progress.AbsorbedPolicyDenials, "; "),
		})
	}
	passIndex := 0
	boundaryIndex := 0
	for _, evidence := range progress.VerificationEvidence {
		if evidence.Status == "tool_error" {
			continue
		}
		if evidence.Status == "passed" {
			passIndex++
			title := localOutcomeText(russian, "Автоматическая проверка прошла", "Automated verification passed")
			if evidence.Targeted {
				title = localOutcomeText(russian, "Изолированный тест прошёл", "Isolated test passed")
			}
			criteria = append(criteria, TaskCompletionCriterion{
				ID:       fmt.Sprintf("verification_pass_%d", passIndex),
				Title:    title,
				State:    "met",
				Evidence: "PASS — `" + evidence.Command + "`" + completionEvidenceDetail(evidence.Detail),
			})
			continue
		}
		boundaryIndex++
		title := localOutcomeText(russian, "Полный набор тестов подтверждён", "Complete test suite verified")
		if evidence.Targeted {
			title = localOutcomeText(russian, "Целевая проверка подтверждена", "Targeted verification passed")
		}
		criteria = append(criteria, TaskCompletionCriterion{
			ID:       fmt.Sprintf("verification_boundary_%d", boundaryIndex),
			Title:    title,
			State:    "unmet",
			Evidence: completionVerificationStatus(evidence.Status) + " — `" + evidence.Command + "`" + completionEvidenceDetail(evidence.Detail),
		})
	}
	return criteria
}

func completionVerificationStatus(status string) string {
	if status == "timed_out" {
		return "TIMEOUT"
	}
	return strings.ToUpper(status)
}

func completionEvidenceDetail(detail string) string {
	detail = strings.TrimSpace(detail)
	if detail == "" {
		return ""
	}
	return "; " + detail
}

func mergeCompletionCriteria(primary, secondary []TaskCompletionCriterion) []TaskCompletionCriterion {
	result := append([]TaskCompletionCriterion(nil), primary...)
	for _, candidate := range secondary {
		duplicate := false
		for _, existing := range result {
			if candidate.ID != "" && existing.ID == candidate.ID {
				duplicate = true
				break
			}
			if strings.EqualFold(strings.TrimSpace(existing.Title), strings.TrimSpace(candidate.Title)) {
				duplicate = true
				break
			}
		}
		if !duplicate {
			result = append(result, candidate)
		}
	}
	return result
}

func removeGenericIncompleteCriteria(criteria []TaskCompletionCriterion) []TaskCompletionCriterion {
	filtered := make([]TaskCompletionCriterion, 0, len(criteria))
	for _, criterion := range criteria {
		title := strings.ToLower(strings.TrimSpace(criterion.Title))
		if criterion.ID == "requested_outcome" || title == "requested outcome delivered" || title == "запрошенный результат получен" {
			continue
		}
		filtered = append(filtered, criterion)
	}
	return filtered
}

func filterCompletionBlockers(blockers []string, progress agentRunProgress) []string {
	unresolved := progress.unresolvedBlockingErrors()
	var filtered []string
	for _, blocker := range blockers {
		blocker = strings.TrimSpace(blocker)
		if blocker == "" {
			continue
		}
		if progress.hasMaterialEvidence() && isGenericCompletionContractBlocker(blocker) {
			continue
		}
		historical := false
		for _, toolErr := range progress.LastErrors {
			if strings.Contains(strings.ToLower(blocker), strings.ToLower(toolErr)) || strings.Contains(strings.ToLower(toolErr), strings.ToLower(blocker)) {
				historical = true
				break
			}
		}
		if historical && progress.hasMaterialEvidence() {
			stillUnresolved := false
			for _, active := range unresolved {
				if strings.Contains(strings.ToLower(active), strings.ToLower(blocker)) || strings.Contains(strings.ToLower(blocker), strings.ToLower(firstLineAfterToolName(active))) {
					stillUnresolved = true
					break
				}
			}
			if !stillUnresolved {
				continue
			}
		}
		filtered = appendLimitedUnique(filtered, blocker, 12)
	}
	return filtered
}

func firstLineAfterToolName(value string) string {
	if index := strings.Index(value, ":"); index >= 0 {
		return strings.TrimSpace(value[index+1:])
	}
	return strings.TrimSpace(value)
}

func isGenericCompletionContractBlocker(value string) bool {
	lower := strings.ToLower(strings.TrimSpace(value))
	return strings.Contains(lower, "assistant_preamble_without_action") ||
		strings.Contains(lower, "completion_flag_missing") ||
		strings.Contains(lower, "did not produce an accepted completion contract")
}

// reconcileCompletionWithAbsorbedPolicyDenials removes the parts of a model's
// self-authored terminal contract that exist only because the run hit an
// immutable write-scope / protected-path boundary. The host, not the model, is
// the source of truth: every neutralized item must map to a policy denial that
// Orion itself recorded while blocking a mutation (progress.AbsorbedPolicyDenials).
//
// A permanently denied side effect is not unfinished work — it can never succeed
// in this run — so it must not burn the terminal-contract budget (which surfaces
// as terminal_contract_failed) or downgrade an otherwise complete primary task to
// partial/blocked. Genuinely outstanding work (any blocker, remaining action, or
// unmet criterion that does not map to a host denial) is preserved untouched, and
// any status promotion still passes through every completed-path gate
// (verification receipts, unresolved tool failures, manual actions).
func reconcileCompletionWithAbsorbedPolicyDenials(completion *TaskCompletion, progress *agentRunProgress) bool {
	if completion == nil || progress == nil || progress.AbsorbedPolicyDenialCount == 0 {
		return false
	}
	denied := absorbedPolicyDenialSignals(progress.AbsorbedPolicyDenials)
	changed := false

	blockers := make([]string, 0, len(completion.Blockers))
	for _, blocker := range completion.Blockers {
		if denied.matches(blocker) {
			changed = true
			continue
		}
		blockers = append(blockers, blocker)
	}
	completion.Blockers = blockers

	remaining := make([]string, 0, len(completion.RemainingActions))
	for _, action := range completion.RemainingActions {
		if denied.matches(action) {
			changed = true
			continue
		}
		remaining = append(remaining, action)
	}
	completion.RemainingActions = remaining

	boundaryEvidence := strings.Join(progress.AbsorbedPolicyDenials, "; ")
	for index := range completion.Criteria {
		criterion := &completion.Criteria[index]
		if strings.EqualFold(strings.TrimSpace(criterion.State), "met") {
			continue
		}
		if denied.matches(criterion.Title) || denied.matches(criterion.Evidence) {
			criterion.State = "met"
			if strings.TrimSpace(criterion.Evidence) == "" || denied.matches(criterion.Evidence) {
				criterion.Evidence = "Immutable safety boundary preserved: " + boundaryEvidence
			}
			changed = true
		}
	}

	if !changed {
		return false
	}

	hasBlockers := len(completion.Blockers) > 0
	hasRemaining := len(completion.RemainingActions) > 0
	unmet := 0
	for _, criterion := range completion.Criteria {
		if !strings.EqualFold(strings.TrimSpace(criterion.State), "met") {
			unmet++
		}
	}

	if completion.Status != "completed" && !hasBlockers && !hasRemaining && unmet == 0 {
		// The immutable boundary was the only thing keeping this non-terminal.
		// Complete the authorized primary task; completed-path gates still run.
		completion.Status = "completed"
		completion.Reason = "criteria_met"
		return true
	}

	if completion.Status != "completed" && !hasBlockers && !hasRemaining && unmet > 0 {
		// A non-terminal status still requires a blocker or remaining action.
		// Keep a valid partial/blocked shape without reviving the boundary noise.
		completion.RemainingActions = []string{unmetCriteriaRemainingAction(completion.Criteria)}
	}
	return true
}

// absorbedDenialSignals captures the host-observed fingerprints of immutable
// policy denials: the exact target paths Orion blocked plus the fixed sentinel
// phrasing it uses when reporting the boundary. Both are host-generated, so a
// match proves the model is describing a denied action rather than real work.
type absorbedDenialSignals struct {
	paths []string
}

var absorbedPolicyDenialSentinels = []string{
	"immutable policy",
	"immutable safety boundary",
	"immutable execution policy",
	"outside writable paths",
	"outside the writable",
	"write scope",
	"write-scope",
	strings.ToLower(policyDenialWriteScope),
	strings.ToLower(policyDenialProtectedPath),
	strings.ToLower(policyDenialPathOutsideWorkspace),
	"policy_denied",
	"policy denied",
	"protected path",
	"protected git metadata",
}

func absorbedPolicyDenialSignals(lines []string) absorbedDenialSignals {
	var signals absorbedDenialSignals
	const marker = "target="
	for _, line := range lines {
		index := strings.Index(strings.ToLower(line), marker)
		if index < 0 {
			continue
		}
		value := strings.TrimSpace(line[index+len(marker):])
		if cut := strings.IndexAny(value, " \t"); cut >= 0 {
			value = value[:cut]
		}
		value = strings.ToLower(strings.TrimSpace(value))
		if value != "" {
			signals.paths = appendLimitedUnique(signals.paths, value, 20)
		}
	}
	return signals
}

func (s absorbedDenialSignals) matches(text string) bool {
	lower := strings.ToLower(strings.TrimSpace(text))
	if lower == "" {
		return false
	}
	for _, path := range s.paths {
		if path != "" && strings.Contains(lower, path) {
			return true
		}
	}
	for _, sentinel := range absorbedPolicyDenialSentinels {
		if sentinel != "" && strings.Contains(lower, sentinel) {
			return true
		}
	}
	return false
}

func unmetCriteriaRemainingAction(criteria []TaskCompletionCriterion) string {
	var titles []string
	for _, criterion := range criteria {
		if strings.EqualFold(strings.TrimSpace(criterion.State), "met") {
			continue
		}
		title := strings.TrimSpace(criterion.Title)
		if title == "" {
			title = strings.TrimSpace(criterion.Evidence)
		}
		if title != "" {
			titles = appendLimitedUnique(titles, title, 3)
		}
	}
	if len(titles) == 0 {
		return "Resolve the outstanding acceptance criterion before completing."
	}
	return "Resolve the outstanding acceptance criteria: " + strings.Join(titles, "; ")
}

func normalizeChangedFileList(values []string) []string {
	var normalized []string
	for _, value := range values {
		value = normalizeReportedPath(value)
		if value == "" || value == "." {
			continue
		}
		normalized = appendLimitedUnique(normalized, value, 40)
	}
	filtered := make([]string, 0, len(normalized))
	for _, candidate := range normalized {
		redundant := false
		for _, other := range normalized {
			if candidate == other {
				continue
			}
			if !strings.Contains(candidate, "/") && strings.HasSuffix(other, "/"+candidate) {
				redundant = true
				break
			}
			if strings.HasPrefix(other, strings.TrimSuffix(candidate, "/")+"/") {
				redundant = true
				break
			}
		}
		if !redundant {
			filtered = append(filtered, candidate)
		}
	}
	return filtered
}

func evidenceAwareSummary(summary string, progress agentRunProgress, russian bool) string {
	trimmed := strings.TrimSpace(summary)
	lower := strings.ToLower(trimmed)
	generic := trimmed == "" || strings.Contains(lower, "did not produce an accepted completion contract") ||
		strings.Contains(lower, "stopped before every requested outcome") || strings.Contains(lower, "requested outcome is not complete")
	if len(progress.FilesChanged) > 0 && progress.hasPassedVerification() {
		if generic || russian {
			if progress.hasUnverifiedBoundary() {
				return localOutcomeText(russian,
					"Исправление реализовано, изолированный тест прошёл. Полный набор тестов не подтверждён из-за отдельной границы проверки.",
					"The fix was implemented and the isolated test passed. The complete suite remains unverified because of a separate verification boundary.")
			}
			return localOutcomeText(russian,
				"Исправление реализовано и подтверждено автоматической проверкой.",
				"The fix was implemented and confirmed by automated verification.")
		}
	}
	if !generic {
		return trimmed
	}
	return trimmed
}

func filterReportedVerification(values []string, progress agentRunProgress) []string {
	var filtered []string
	for _, value := range values {
		lower := strings.ToLower(strings.TrimSpace(value))
		covered := false
		for _, evidence := range progress.VerificationEvidence {
			command := strings.ToLower(strings.TrimSpace(evidence.Command))
			if command != "" && (lower == command || strings.Contains(lower, command)) {
				covered = true
				break
			}
		}
		if !covered {
			filtered = appendLimitedUnique(filtered, value, 20)
		}
	}
	return filtered
}

func localizeKnownCompletionBoilerplate(completion TaskCompletion) TaskCompletion {
	replacements := map[string]string{
		"Orion stopped a proven semantic loop after bounded strategy recovery produced no new evidence.":                                        "Не удалось получить запрошенный результат: повторные действия не дали новых данных. Цикл остановлен.",
		"Use another available source or tool, or specify the missing input. Repeating the blocked action cannot complete this task.":           "Нужен другой доступный источник или инструмент либо недостающие исходные данные. Повтор заблокированного действия не поможет.",
		"The task stopped before every requested outcome was delivered.":                                                                        "Задача остановилась до подтверждения всех запрошенных результатов.",
		"The agent did not produce an accepted completion contract.":                                                                            "Агент не сформировал корректный контракт завершения.",
		"The read-only agent did not produce an accepted completion contract.":                                                                  "Агент чтения не сформировал корректный контракт завершения.",
		"The agent stopped after repeating the same action without new evidence.":                                                               "Агент остановлен после повторения одного действия без новых данных.",
		"The read-only agent stopped after repeating an unchanged tool call.":                                                                   "Агент чтения остановлен после повторения неизменившегося вызова инструмента.",
		"The requested outcome is not complete.":                                                                                                "Запрошенный результат подтверждён не полностью.",
		"Continue from the saved context and resolve the blocker without restarting discovery.":                                                 "Продолжить из сохранённого контекста и устранить оставшийся блокер без повторного исследования.",
		"Rerun only the unverified complete test suite from the saved checkpoint; do not repeat the successful isolated test or investigation.": "Повторить только неподтверждённый полный набор тестов из сохранённого состояния; уже прошедший изолированный тест и исследование не повторять.",
	}
	if translated, ok := replacements[completion.Summary]; ok {
		completion.Summary = translated
	}
	for index, criterion := range completion.Criteria {
		switch strings.ToLower(strings.TrimSpace(criterion.Title)) {
		case "requested outcome delivered":
			completion.Criteria[index].Title = "Запрошенный результат получен"
			if criterion.State != "met" {
				completion.Criteria[index].Title = "Запрошенный результат не получен"
			}
		case "required action completed":
			completion.Criteria[index].Title = "Обязательное действие выполнено"
		}
	}
	for index, action := range completion.RemainingActions {
		if translated, ok := replacements[action]; ok {
			completion.RemainingActions[index] = translated
		}
	}
	for index, blocker := range completion.Blockers {
		if translated, ok := replacements[blocker]; ok {
			completion.Blockers[index] = translated
		}
	}
	return completion
}

func reconcileTaskCompletion(
	completion *TaskCompletion,
	progress agentRunProgress,
	validation *executorValidationResult,
	sessionID string,
	originalPrompt string,
) (*TaskCompletion, bool) {
	if completion == nil || completion.Status != "completed" {
		return completion, false
	}
	blockers := progress.unresolvedBlockingErrors()
	blockers = append(blockers, progress.ManualActions...)
	if validation != nil && !validation.OK {
		blockers = append(blockers, strings.TrimSpace(firstNonEmptyString(validation.Feedback, validation.Status)))
	}
	blockers = cleanCompletionStrings(blockers, 12)
	if len(blockers) == 0 {
		return completion, false
	}

	copyValue := *completion
	copyValue.Status = "partial"
	copyValue.Reason = "unresolved_execution_state"
	copyValue.Summary = localOutcomeText(
		outcomeInRussian(originalPrompt),
		"Основной результат подготовлен, но задача не может считаться полностью завершённой из-за оставшегося блокера.",
		"The main result is prepared, but the task cannot be considered fully complete while a required blocker remains.",
	)
	copyValue.Criteria = append(append([]TaskCompletionCriterion{}, completion.Criteria...), TaskCompletionCriterion{
		ID:       "required_execution",
		Title:    localOutcomeText(outcomeInRussian(originalPrompt), "Обязательное действие выполнено", "Required action completed"),
		State:    "unmet",
		Evidence: strings.Join(blockers, "; "),
	})
	copyValue.Blockers = mergeCompletionStrings(copyValue.Blockers, blockers, 12)
	copyValue.RemainingActions = mergeCompletionStrings(copyValue.RemainingActions, progress.ManualActions, 12)
	if len(copyValue.RemainingActions) == 0 {
		copyValue.RemainingActions = []string{localOutcomeText(
			outcomeInRussian(originalPrompt),
			"Устранить блокер и продолжить с сохранённого checkpoint без повторного исследования.",
			"Resolve the blocker and continue from the saved checkpoint without restarting discovery.",
		)}
	}
	copyValue.Explicit = completion.Explicit
	return finalizeTaskCompletion(&copyValue, progress, sessionID, originalPrompt), true
}

func renderTaskCompletion(completion *TaskCompletion, progress agentRunProgress, originalPrompt string) string {
	completion = finalizeTaskCompletion(completion, progress, "", originalPrompt)
	if completion == nil {
		return ""
	}
	// Session.Outcome keeps the structured evidence. The response is the
	// user-facing payload and must remain intact for drafts and investigations.
	if response := strings.TrimSpace(completion.Response); response != "" {
		return response
	}
	russian := outcomeInRussian(originalPrompt)
	status := map[string][2]string{
		"completed": {"выполнено", "completed"},
		"partial":   {"частично выполнено", "partially completed"},
		"blocked":   {"заблокировано", "blocked"},
	}[completion.Status]
	var b strings.Builder
	b.WriteString("# " + localOutcomeText(russian, "Итог задачи", "Task outcome") + "\n")
	b.WriteString(localOutcomeText(russian, "Статус: ", "Status: ") + localOutcomeText(russian, status[0], status[1]) + "\n")
	b.WriteString("\n## " + localOutcomeText(russian, "Краткий итог", "Summary") + "\n" + completion.Summary + "\n")
	b.WriteString("\n## " + localOutcomeText(russian, "Критерии завершения", "Completion criteria") + "\n")
	for _, criterion := range completion.Criteria {
		mark := "✓"
		if criterion.State != "met" {
			mark = "✗"
		}
		fmt.Fprintf(&b, "- %s %s — %s\n", mark, criterion.Title, criterion.Evidence)
	}
	if len(completion.ChangedFiles) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Изменённые файлы", "Changed files") + "\n")
		for _, path := range completion.ChangedFiles {
			b.WriteString("- " + path + "\n")
		}
	}
	if len(completion.Artifacts) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Артефакты", "Artifacts") + "\n")
		for _, artifact := range completion.Artifacts {
			value := firstNonEmptyString(artifact.Path, artifact.URL, artifact.Title)
			if value != "" {
				b.WriteString("- " + value + "\n")
			}
		}
	}
	if len(completion.Verification) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Проверки", "Verification") + "\n")
		for _, check := range completion.Verification {
			b.WriteString("- " + check + "\n")
		}
	}
	if len(completion.Unverified) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Неподтверждённые границы", "Unverified boundaries") + "\n")
		for _, boundary := range completion.Unverified {
			b.WriteString("- " + boundary + "\n")
		}
	}
	if len(completion.GitActions) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Действия Git", "Git actions") + "\n")
		for _, action := range completion.GitActions {
			b.WriteString("- " + action + "\n")
		}
	}
	if len(completion.Blockers) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Не завершено / блокеры", "Not completed / blockers") + "\n")
		for _, blocker := range completion.Blockers {
			b.WriteString("- " + blocker + "\n")
		}
	}
	if len(completion.RemainingActions) > 0 {
		headingRU, headingEN := "Что осталось", "Remaining work"
		for _, action := range completion.RemainingActions {
			lower := strings.ToLower(action)
			if strings.Contains(lower, "manual") || strings.Contains(lower, "вручн") || strings.Contains(lower, "ручн") {
				headingRU, headingEN = "Требуется ручное действие", "Manual action required"
				break
			}
		}
		b.WriteString("\n## " + localOutcomeText(russian, headingRU, headingEN) + "\n")
		for _, action := range completion.RemainingActions {
			b.WriteString("- " + action + "\n")
		}
	}
	if len(completion.Recommendations) > 0 {
		b.WriteString("\n## " + localOutcomeText(russian, "Рекомендуемые действия", "Recommended actions") + "\n")
		for index, action := range completion.Recommendations {
			fmt.Fprintf(&b, "%d. %s\n", index+1, action)
		}
	}
	return strings.TrimSpace(b.String())
}

func completionActions(completion *TaskCompletion) []TaskCompletionAction {
	if completion == nil {
		return nil
	}
	if strings.EqualFold(strings.TrimSpace(completion.ExecutionResult), "NEEDS_INPUT") {
		for _, item := range completion.RemainingActions {
			fields := strings.Fields(item)
			for index := 0; index+2 < len(fields); index++ {
				if !strings.EqualFold(strings.TrimSuffix(fields[index], ":"), "reconcile-intent") {
					continue
				}
				intentID := strings.TrimSpace(fields[index+1])
				if intentID == "" {
					continue
				}
				return []TaskCompletionAction{
					{
						ID:      "reconcile-applied",
						Label:   "Confirm action was applied",
						Kind:    "reconcile_intent",
						Prompt:  "orion-action: reconcile-intent " + intentID + " applied",
						Primary: true,
					},
					{
						ID:     "reconcile-not-applied",
						Label:  "Confirm action was not applied",
						Kind:   "reconcile_intent",
						Prompt: "orion-action: reconcile-intent " + intentID + " not_applied",
					},
					{
						ID:     "reconcile-abort",
						Label:  "Abort the pending action",
						Kind:   "reconcile_intent",
						Prompt: "orion-action: reconcile-intent " + intentID + " abort",
					},
				}
			}
		}
	}
	var actions []TaskCompletionAction
	for index, item := range completion.RemainingActions {
		actions = append(actions, TaskCompletionAction{ID: fmt.Sprintf("remaining-%d", index+1), Label: strings.TrimSpace(item), Kind: "continue", Prompt: item, Primary: index == 0})
	}
	for index, item := range completion.Recommendations {
		actions = append(actions, TaskCompletionAction{ID: fmt.Sprintf("recommendation-%d", index+1), Label: strings.TrimSpace(item), Kind: "follow_up", Prompt: item})
	}
	return actions
}

func cleanCompletionStrings(values []string, limit int) []string {
	var out []string
	for _, value := range values {
		out = appendLimitedUnique(out, strings.TrimSpace(value), limit)
	}
	return out
}

func mergeCompletionStrings(primary, secondary []string, limit int) []string {
	out := cleanCompletionStrings(primary, limit)
	for _, value := range secondary {
		out = appendLimitedUnique(out, value, limit)
	}
	return out
}

func cleanCompletionArtifacts(values []TaskCompletionArtifact) []TaskCompletionArtifact {
	seen := map[string]bool{}
	out := make([]TaskCompletionArtifact, 0, len(values))
	for _, value := range values {
		value.Kind = strings.TrimSpace(value.Kind)
		value.Path = strings.TrimSpace(value.Path)
		value.URL = strings.TrimSpace(value.URL)
		value.Title = strings.TrimSpace(value.Title)
		key := value.Kind + "\x00" + value.Path + "\x00" + value.URL + "\x00" + value.Title
		if (value.Path == "" && value.URL == "" && value.Title == "") || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, value)
	}
	return out
}

func defaultCompletionReason(status string) string {
	switch status {
	case "completed":
		return "criteria_met"
	case "partial":
		return "partial"
	default:
		return "blocked"
	}
}

var deferredActionPhrases = []string{
	"let me ", "i will ", "i'll ", "going to ", "first i will ",
	"сейчас проверю", "сейчас соберу", "сейчас изучу", "начну с ",
	"давайте сначала", "перейду к ", "соберу контекст", "проанализирую в параллель",
}

func looksLikeDeferredAction(content string) bool {
	trimmed := strings.TrimSpace(content)
	if trimmed == "" || looksLikeTaskOutcome(trimmed) {
		return false
	}
	// Native-tool providers occasionally print Orion's diagnostic CALL/ARGS
	// notation as prose. It is never an executed action and must remain in the
	// bounded protocol-recovery loop even when the payload is long.
	if containsTextToolCallEnvelope(trimmed) {
		return true
	}
	if len(trimmed) > 1600 {
		return false
	}
	lower := strings.ToLower(trimmed)
	for _, phrase := range deferredActionPhrases {
		if strings.Contains(lower, phrase) {
			return true
		}
	}
	return false
}
