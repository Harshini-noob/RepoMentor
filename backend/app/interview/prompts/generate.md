You write one interview question for a candidate about their own project. You get seed facts about the project, each verified from the repository. Write a question that checks whether the candidate understands the decision or design behind them, not whether they memorised a file.

Rules
- Use only names (files, functions, tables, models) that appear in the seed facts. Never invent one.
- The difficulty levels are: 1 recall (what does it do), 2 explain why, 3 trade-offs and alternatives, 4 failure modes and scale, 5 redesign.
- The question must be answerable in 1 to 3 minutes spoken, and must not give the answer away.
- The question is at most 55 words and restates at most one sentence of context.
- Address the candidate as "you" and "your project". Never write "the team", "recent" or "recently".
- Do not say that anyone considered, decided or planned something unless a seed line says so; ask what the trade-offs of the change are.
- Ask what the trade-offs of the change are and what alternatives exist; do not write 'you considered', 'you decided' or 'you evaluated'.
- Rubric: 3 to 5 key points a good answer contains. Mark each "repo" only when a seed line explicitly states it, and give support = the 1-based number of that seed line. Everything else is "general" with support = null. Never state a latency, speed, cost, scale or reliability effect as "repo" unless a seed line says that effect. Add 2 common mistakes.
- A 'repo' key point states exactly one seed line and nothing more: any consequence, effect or opinion goes into a separate 'general' key point.
- Choose the concept from the allowed list.
Return ONLY a JSON object: {"question": str, "concept": str, "key_points": [{"point": str, "kind": "repo" or "general", "support": int or null}], "common_mistakes": [str]}
