export const meta = {
  name: 'implement',
  description:
    'Fan out cursor-headless workers (cursor_ask/plan/implement) for a clear-spec task; Claude parent integrates',
  whenToUse:
    'Invoked by /cursor-implement when the Workflow tool is available. Requires args {task, cwd}. Optional slices: [{goal, tool?, model?, worktree?}]. Returns worker summaries for the parent to integrate.',
  phases: [
    { title: 'Decompose', detail: 'split task into independent slices if not provided' },
    { title: 'Workers', detail: 'one thin Claude agent per slice; each must call cursor_* MCP' },
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

const task = ARGS && ARGS.task
const cwd = ARGS && ARGS.cwd
if (!task || typeof task !== 'string' || !task.trim()) {
  throw new Error('implement workflow requires args: {task: "<assignment>", cwd: "<workspace>"}')
}
if (!cwd || typeof cwd !== 'string') {
  throw new Error('implement workflow requires args.cwd (absolute workspace path)')
}

const TOOLS = new Set(['cursor_implement', 'cursor_ask', 'cursor_plan'])
const MODELS = new Set([
  'composer-2.5',
  'composer-2.5-fast',
  'cursor-grok-4.5-low',
  'cursor-grok-4.5-low-fast',
  'cursor-grok-4.5-medium',
  'cursor-grok-4.5-medium-fast',
  'cursor-grok-4.5-high',
  'cursor-grok-4.5-high-fast',
])

function normalizeSlice(s, i) {
  if (!s || typeof s !== 'object') return null
  const goal = String(s.goal || s.prompt || '').trim()
  if (!goal) return null
  let tool = String(s.tool || 'cursor_implement').trim()
  if (!TOOLS.has(tool)) tool = 'cursor_implement'
  let model = String(s.model || 'composer-2.5').trim()
  if (!MODELS.has(model)) model = 'composer-2.5'
  const worktree =
    s.worktree === undefined || s.worktree === null
      ? tool === 'cursor_implement'
        ? `cursor-impl-${i + 1}`
        : null
      : s.worktree
  // A `*-fast` model id already encodes the fast variant; keep the id verbatim and
  // mirror the intent into the flag so the two can never disagree. Composer stays
  // fast-by-default (mechanical work) unless the caller explicitly opts out.
  const fast =
    s.fast === undefined || s.fast === null
      ? model.endsWith('-fast') || model.startsWith('composer')
      : Boolean(s.fast)
  return { goal, tool, model, worktree, fast }
}

const SLICE_SCHEMA = {
  type: 'object',
  required: ['slices'],
  properties: {
    slices: {
      type: 'array',
      items: {
        type: 'object',
        required: ['goal', 'tool', 'model'],
        properties: {
          goal: { type: 'string' },
          tool: {
            type: 'string',
            enum: ['cursor_implement', 'cursor_ask', 'cursor_plan'],
          },
          model: { type: 'string' },
          worktree: { type: 'string' },
          fast: { type: 'boolean' },
        },
      },
    },
  },
}

const WORKER_SCHEMA = {
  type: 'object',
  required: ['tool', 'model', 'summary', 'ok'],
  properties: {
    tool: { type: 'string' },
    model: { type: 'string' },
    worktree: { type: 'string' },
    ok: { type: 'boolean' },
    summary: {
      type: 'string',
      description: 'Compact structured summary from the cursor-headless worker',
    },
    changedFiles: { type: 'array', items: { type: 'string' } },
    risks: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
}

const MCP_DISCIPLINE = `
You are a thin wrapper. You MUST use the cursor-headless MCP tools
(cursor_ask / cursor_plan / cursor_implement). Do NOT implement edits with your
own Write/Edit tools. Do NOT run tests, installs, builds, or dev servers unless
the slice prompt explicitly requires it.

Model routing is already decided for you. Pass the MCP arguments EXACTLY as given
below — copy the model id verbatim, including any "-fast" suffix. Do NOT split,
shorten, normalize, or substitute the model id, and do NOT re-derive the fast flag.
Choosing a different model than the one specified is a failure of this slice.

Return a compact summary only — workers do not see parent history.
Report the model id you actually passed, verbatim, in the "model" field.
`

let slices = Array.isArray(ARGS.slices)
  ? ARGS.slices.map(normalizeSlice).filter(Boolean)
  : []

if (slices.length === 0) {
  phase('Decompose')
  log('No slices provided — decomposing task into parallel cursor-headless workers')
  const plan = await agent(
    `Decompose this implementation task into 2–6 independent slices for
cursor-headless workers. Each slice must be self-contained.

Task:
<<<TASK
${task}
TASK>>>

Routing:
- cursor_ask = read-only Q&A
- cursor_plan = read-only explore/design
- cursor_implement = writes (default for most slices)
- Models: composer-2.5 (mechanical/default), cursor-grok-4.5-low|medium|high by complexity
- Prefer fast=true / *-fast when latency matters
- Suggest a short worktree name for each implement slice

Return JSON slices only.`,
    {
      label: 'decompose',
      phase: 'Decompose',
      schema: SLICE_SCHEMA,
    },
  )
  slices = ((plan && plan.slices) || []).map(normalizeSlice).filter(Boolean)
}

if (slices.length === 0) {
  throw new Error('Decomposition produced no slices — provide args.slices or a clearer task')
}

// Cap fan-out; runtime also enforces concurrency
if (slices.length > 8) {
  log(`Capping slices from ${slices.length} to 8`)
  slices = slices.slice(0, 8)
}

phase('Workers')
log(`Launching ${slices.length} cursor-headless worker(s) under ${cwd}`)

const results = await parallel(
  slices.map((slice, i) => () =>
    agent(
      `${MCP_DISCIPLINE}

Slice ${i + 1}/${slices.length}
Tool: ${slice.tool}

Call ${slice.tool} with EXACTLY these argument values, copied verbatim
(add only your expanded \`prompt\`):
${JSON.stringify(
  {
    model: slice.model,
    fast: slice.fast,
    cwd,
    ...(slice.tool === 'cursor_implement' && slice.worktree
      ? { worktree: slice.worktree }
      : {}),
  },
  null,
  2,
)}${
        slice.tool === 'cursor_implement' && !slice.worktree
          ? '\n(no worktree — this slice runs in-tree)'
          : ''
      }

Overall task (context only):
<<<TASK
${task}
TASK>>>

Your slice goal:
<<<GOAL
${slice.goal}
GOAL>>>

Call the MCP tool now with a self-contained prompt derived from the goal.
Then return ok/summary/changedFiles/risks/error.`,
      {
        label: `worker:${i + 1}:${slice.tool}`,
        phase: 'Workers',
        schema: WORKER_SCHEMA,
      },
    ).then(r =>
      r
        ? {
            index: i + 1,
            goal: slice.goal,
            requested: slice,
            ...r,
          }
        : {
            index: i + 1,
            goal: slice.goal,
            requested: slice,
            ok: false,
            tool: slice.tool,
            model: slice.model,
            summary: '',
            error: 'worker agent returned no result',
          },
    ),
  ),
)

const workers = results.filter(Boolean)
const failed = workers.filter(w => !w.ok)
log(
  `Workers done: ${workers.length - failed.length}/${workers.length} ok` +
    (failed.length ? `; failed: ${failed.map(w => w.index).join(', ')}` : ''),
)

return {
  cwd,
  task,
  workers,
  failedIndexes: failed.map(w => w.index),
  note:
    'Parent session must integrate worker summaries into the user-facing result. Do not re-read whole worker diffs unless needed.',
}
