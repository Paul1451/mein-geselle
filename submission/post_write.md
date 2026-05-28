# Hermes Has a Learning Loop — Here's What It Took To Actually Make It Fire

*This is a submission for the [Hermes Agent Challenge](https://dev.to/challenges/hermes-agent-2026-05-15), Write About Hermes Agent.*

## The promise vs. the reality

The Hermes documentation makes a quiet, beautiful promise: the agent generates skills from experience, patches them while it works, and nudges itself to persist knowledge it has just used. In `agent/prompt_builder.py`, the `SKILLS_GUIDANCE` block (lines 166–173) tells the planner to save approaches after complex tasks and to patch outdated skills immediately. Read it once and you'll believe the loop closes itself.

Then I shipped Mein Geselle — a German-language Telegram bot for painters — to my own DeepSeek v4 Pro planner via OpenRouter and asked it to "Bei Frau Müller immer 5% Skonto geben" ("For Mrs. Müller, always 5% discount").

The agent replied, in perfect German, *"Verstanden, ich merke mir das."* Understood, I'll remember that.

I checked. `ls -la skills/angebot_style/SKILL.md` showed the same mtime as five minutes ago. `git log` in the skills repo showed no new commit. The agent had spoken the right words and done absolutely nothing. The loop *looked* closed. It wasn't.

This essay is about why that happens, the specific Hermes internals that explain it, and the 200-line wrapper that finally made my planner reach for the skill system on its own.

## Why didn't `skill_manage` fire?

Three reasons, in the order I discovered them.

**1. System-prompt nudges aren't deterministic.** `SKILLS_GUIDANCE` is good prose, but it talks about "complex tasks" and "non-trivial workflows". A user saying "merk dir das" is neither — it's a casual one-liner. Lighter planners pattern-match the guidance against "5+ tool calls" and "tricky errors", see no match, and skip the call. The prompt is true; it isn't a trigger.

**2. The `skill_manage` schema is verbose.** It exposes three actions (`edit`, `patch`, `write_file`), validates slot frontmatter, supports optional semver bumps, and accepts a multi-line markdown body. For a tier-2 planner in the middle of a customer-CRM conversation, that schema is expensive: every additional parameter is another decision the model has to justify. The path of least resistance is *reply, don't tool-call*. Tier-1 models punch through this; tier-2 models flinch.

**3. The tool registry uses AST discovery, not runtime introspection.** This one cost me an hour. In `tools/registry.py:42–54`, `_module_registers_tools` parses each tool file as Python AST and walks only the top-level body looking for a literal `registry.register(...)` call. The predicate that decides what counts is `_is_registry_register_call` at lines 29–39:

```python
def _is_registry_register_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )
```

It must be `registry.register(...)`, at module top level, as a bare expression. My first version of `remember_rule` wrapped the call in `try: registry.register(...); except ImportError: pass` so the file would still be importable standalone for tests. The `try` block is no longer a top-level `ast.Expr` — it's an `ast.Try` whose body contains an `ast.Expr`. The registry walked past my tool. The agent never saw it. It was silently skipped, with no warning in the logs, because the AST scan only ever returns names of modules it *did* recognize. If you write Hermes tools and your module isn't loading, check this first.

## The fix: a thin learning-loop primitive

Once I understood the three failure modes, the design wrote itself. I built `remember_rule`. The whole point is that it lives one level *below* `skill_manage` in terms of capability, but one level *above* it in terms of how easy it is to invoke. The schema description starts with this exact sentence:

> "Persist a rule, preference, or correction the user just gave you (e.g. 'merk dir das', 'always do X', 'never Y')."

That phrase is *literally what the user types*. The planner doesn't need to map "casual correction" onto "complex task" anymore — the trigger words are in the schema. The required parameter is one string, `rule`. Optional are `scope` (e.g. `customer:Müller`), `category` (`quoting`, `emergency`, `tone`, …), and `source_msg`. That's it.

The handler is roughly:

```python
def handle(args):
    skill_file = CATEGORY_MAP[args["category"]]    # quoting -> angebot_style/SKILL.md
    append_under_h2(skill_file, "## 📒 Learned Rules",
        f"- {today_iso()} [{args['scope']}] "
        f"({args['category']}): {args['rule']}")
    bump_patch_version(skill_file)                 # 1.2.3 -> 1.2.4 in YAML
    git_commit(skill_file, f"remember_rule: {args['rule'][:60]}")
    return {"ok": True, "commit": short_sha()}
```

Four side effects, all observable from outside the agent: a file mtime change, a version bump in YAML frontmatter, a new H2 bullet, and a git commit SHA the dashboard can render as a timeline dot.

This is 200 lines of Python sitting on top of Hermes' native `skill_manage`. On paper it's redundant — `skill_manage(action="patch")` could do all of it. In practice it isn't redundant at all. It's a *trigger-shaped wrapper* over a more general primitive. The general primitive stays untouched for cases that genuinely need its full schema; the wrapper handles the high-frequency "user just told me something" case with a schema the planner finds irresistible.

## What this taught me about agent design

Two lessons I'll carry into every Hermes tool I write next.

**Tool shape matters more than tool power.** A planner-LLM picks tools the way a JIT picks instructions — cheap operands, obvious semantics, low arity wins. If you build a sophisticated tool and the planner doesn't reach for it, you usually didn't build the wrong tool. You built the wrong *shape*. Wrap it. Build the affordance the planner is actually looking for, even if it's just a curried view of something more powerful underneath. The wrapper costs you a screen of code and buys you actual invocations.

**Verify the loop, don't trust the agent's reply.** Throughout Hermes development, the agent will tell you `Verstanden, ich merke mir das`. That's chat, not action. The only proof the loop closed is a side effect: a file mtime, a new git commit, a row in a database, a counter that ticked up. Build the side-effect view *before* you trust the loop — in my case, a tiny SVG chart of total skill-LOC per commit on the dashboard. Once you can see the loop close in real time, every regression announces itself.

## What I'd do next

Two ideas I'm sketching for the next iteration.

**Auto-trigger detector.** A tiny watcher (could be a regex, could be a 1B model on-device) that scans each user turn for rule-shaped language — *always X*, *never Y*, *from now on*, *merk dir* — and injects a sentence into the next system prompt: "The user just stated a persistent rule. Call `remember_rule` before replying." It removes the dependency on the planner pattern-matching the trigger by itself.

**Per-customer skill scoping.** My rules currently carry `[customer:Müller]` in the bullet text. The skill system itself doesn't natively scope rules by entity, so every conversation injects every rule. A pre-filter that selects only the rules relevant to the *current* customer before the skill enters context would tighten the prompt and let the planner reason on more specific data without me growing the skill file forever.

## Closing

The Hermes learning loop is real, but it isn't free. The wiring is there; the trigger isn't always. The takeaway, for anyone building on top of Hermes: don't trust the chat reply, watch the side effects, and when a planner won't reach for a tool, reshape the tool before you reshape the prompt.

Project: https://github.com/Paul1451/mein-geselle. The Build post for the same project covers the full architecture; this one was the design lesson worth pulling out on its own.

Tags: hermesagentchallenge, devchallenge, agents
