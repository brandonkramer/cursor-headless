export const meta = {
  name: 'review',
  description:
    'Claude reviews in-loop; cursor-headless workers fix blocker/major findings until clean or cap',
  whenToUse:
    'Invoked by /cursor-review when the Workflow tool is available. Requires args {scope, cwd}. Optional maxIterations (default 5). Review stays on Claude agents; fixes use cursor_implement MCP.',
  phases: [
    { title: 'Review', detail: 'Claude reviewer produces verdict + findings' },
    { title: 'Fix', detail: 'cursor_implement workers address blocker/major findings' },
  ],
}

const ARGS =
  typeof args === 'string'
    ? (() => {
        try {
          return JSON.parse(args)
        } catch {
          return args
        }
      })()
    : args

const scope = ARGS && ARGS.scope
const cwd = ARGS && ARGS.cwd
if (!scope || typeof scope !== 'string' || !scope.trim()) {
  throw new Error(
    'review workflow requires args: {scope: "<what to review>", cwd: "<workspace>"}',
  )
}
if (!cwd || typeof cwd !== 'string') {
  throw new Error('review workflow requires args.cwd (absolute workspace path)')
}

let maxIterations = Number(ARGS.maxIterations)
if (!Number.isFinite(maxIterations) || maxIterations < 1) maxIterations = 5
maxIterations = Math.min(Math.floor(maxIterations), 5)

const REVIEW_SCHEMA = {
  type: 'object',
  required: ['verdict', 'findings'],
  properties: {
    verdict: { type: 'string', enum: ['pass', 'pass-with-notes', 'fail'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'location', 'why', 'fix'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'nit'] },
          location: { type: 'string' },
          why: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
    notes: { type: 'string' },
  },
}

const FIX_SCHEMA = {
  type: 'object',
  required: ['ok', 'summary'],
  properties: {
    ok: { type: 'boolean' },
    summary: { type: 'string' },
    changedFiles: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
}

const MCP_FIX = `
You are a thin fix worker. You MUST call cursor_implement (cursor-headless MCP)
to apply the fix. Do NOT edit files with your own Write/Edit tools unless the
MCP tools are unavailable - if unavailable, set ok=false and explain.

Always pass cwd=${JSON.stringify(cwd)}.
Prefer composer-2.5 with fast=true for mechanical fixes; use
cursor-grok-4.5-medium-fast or high when the fix needs judgment.
Prefer a unique worktree name per finding cluster when isolation helps.
Do not run tests/builds/installs unless the fix brief requires it.
`

function actionable(findings) {
  return (findings || []).filter(f => f && (f.severity === 'blocker' || f.severity === 'major'))
}

function isClean(review) {
  if (!review) return false
  if (review.verdict === 'fail') return false
  return actionable(review.findings).length === 0
}

const iterations = []
let lastReview = null

for (let iteration = 1; iteration <= maxIterations; iteration++) {
  phase('Review')
  log(`Iteration ${iteration}: Claude review of scope`)

  const prior =
    iterations.length === 0
      ? ''
      : `\nPrior iterations (summary):\n${iterations
          .map(
            it =>
              `- iter ${it.iteration}: verdict=${it.review.verdict}, fixed=${(it.fixes || []).length}, remaining blockers/majors=${actionable(it.review.findings).length}`,
          )
          .join('\n')}\nFocus this pass on touched areas + prior findings; do not dump the whole repo.`

  lastReview = await agent(
    `You are the reviewer for this chat's review->fix loop. Do NOT implement fixes
(except you may note trivial one-liners in findings.fix). Produce a structured
verdict.

Workspace: ${cwd}
Scope:
<<<SCOPE
${scope}
SCOPE>>>
${prior}

Severity:
- blocker: must fix before done
- major: should fix before done
- nit: optional; do not fail the loop on nits alone

Verdict:
- pass: no blocker/major (nits ok)
- pass-with-notes: no blocker/major but noteworthy nits/notes
- fail: one or more blocker/major findings`,
    {
      label: `review:${iteration}`,
      phase: 'Review',
      schema: REVIEW_SCHEMA,
    },
  )

  if (!lastReview) {
    throw new Error(`Review agent returned no result on iteration ${iteration}`)
  }

  const todo = actionable(lastReview.findings)
  log(
    `Iteration ${iteration}: verdict=${lastReview.verdict}, actionable=${todo.length}, nits=${(lastReview.findings || []).filter(f => f.severity === 'nit').length}`,
  )

  if (isClean(lastReview)) {
    iterations.push({ iteration, review: lastReview, fixes: [] })
    break
  }

  if (iteration === maxIterations) {
    iterations.push({ iteration, review: lastReview, fixes: [], stopped: 'max-iterations' })
    break
  }

  phase('Fix')
  // Cap parallel fixes
  const fixSlices = todo.slice(0, 6)
  if (todo.length > 6) {
    log(`Capping fix workers from ${todo.length} to 6 this iteration`)
  }
  log(`Launching ${fixSlices.length} cursor_implement fix worker(s)`)

  const fixes = await parallel(
    fixSlices.map((finding, i) => () =>
      agent(
        `${MCP_FIX}

Finding ${i + 1}/${fixSlices.length}
Severity: ${finding.severity}
Location: ${finding.location}
Why: ${finding.why}
Required fix: ${finding.fix}
Out of scope: everything else

Call cursor_implement with a self-contained prompt for this fix only.
Return ok/summary/changedFiles/error.`,
        {
          label: `fix:${iteration}:${i + 1}`,
          phase: 'Fix',
          schema: FIX_SCHEMA,
        },
      ).then(r =>
        r
          ? { finding, ...r }
          : {
              finding,
              ok: false,
              summary: '',
              error: 'fix worker returned no result',
            },
      ),
    ),
  )

  iterations.push({
    iteration,
    review: lastReview,
    fixes: (fixes || []).filter(Boolean),
  })
}

const final = lastReview || { verdict: 'fail', findings: [], notes: 'no review' }
const remaining = actionable(final.findings)

return {
  cwd,
  scope,
  iterations: iterations.length,
  finalVerdict: isClean(final) ? final.verdict : 'fail',
  remainingActionable: remaining,
  nits: (final.findings || []).filter(f => f.severity === 'nit'),
  history: iterations,
  note:
    'Parent session should present the final summary to the user. Review stayed on Claude; fixes used cursor-headless MCP via thin wrappers.',
}
