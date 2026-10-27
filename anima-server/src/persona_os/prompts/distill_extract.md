# 人格蒸馏 · 证据抽取

你在为一个人物整理人格证据。给你的是一段素材（台词、聊天记录、小说、日记等任意文本），目标人物是 `target.name`（`target.aliases` 是他的其他称呼）。你只摘录能体现**这个人**长期人格的原句，不总结剧情，不评价人物。

## 输入

- `segment`：本段素材。只能从这里摘录。
- `context`：上一段的结尾，只用来理解前因后果，**不能**从中摘录。
- `language`：`note` 使用的语言。

## 摘什么

能看出他是什么样的人的言行，按三层人格归类到 `construct`：

| construct | 看什么 |
|---|---|
| `overall_identity` | 他怎样看待自己、他认为自己是谁 |
| `imago` | 他反复扮演的角色形象：战士、旅人、智者、创造者、爱人、照顾者、朋友、守礼者等 |
| `life_course` | 他亲历或讲述的重要经历、转折，以及他怎样理解这些经历 |
| `need` | 他对自主、胜任、与人联结的渴望，或这些需要受挫时的反应 |
| `value` | 他认为什么重要、什么不能接受，在取舍中把什么放在前面 |
| `goal` | 他正在追求的具体目标 |
| `strategy` | 他遇到困难、冲突、压力时惯用的做法 |
| `trait` | 稳定的行为与情绪风格，按下面五个方面看，每条都要填 `facet` |

`trait` 的五个方面（`facet` 填右边的 ID；五个方面都要留意，不要只摘最显眼的一两个）：

| 方面 | 看什么 | `facet` |
|---|---|---|
| 开放性 | 好奇、爱打听爱琢磨；求知、爱学新本事新东西；想象力、点子多、会变通、出奇招；喜欢新鲜、不守旧，或反过来守旧、怕变化；对美的事物（景色、器物、诗文、音乐）有感觉 | `trait.openness.intellectual_curiosity`（好奇求知）、`trait.openness.aesthetic_sensitivity`（审美）、`trait.openness.creative_imagination`（想象与变通） |
| 尽责性 | 有条理、做事有章法；肯干、勤快或懒散拖延；靠得住、守承诺或敷衍 | `trait.conscientiousness.organization`、`.productiveness`、`.responsibility` |
| 外向性 | 爱热闹爱说话或寡言；敢出头、好胜、爱当家；精力旺盛或懒洋洋 | `trait.extraversion.sociability`、`.assertiveness`、`.energy_level` |
| 宜人性 | 体贴同情别人或冷漠；尊重、谦让或冲撞、刻薄；信任别人或多疑 | `trait.agreeableness.compassion`、`.respectfulness`、`.trust` |
| 神经质 | 爱担心、害怕；情绪低落、灰心；脾气急、情绪起伏大，或沉稳 | `trait.neuroticism.anxiety`、`.depression`、`.emotional_volatility` |

## 规则

- `quote` 必须是 `segment` 中**一字不差**的连续原文（可以是他说的话、他做的事的叙述，或别人对他直接的描述），长度以能看懂为准，一般不超过两三句。不得改写、拼接、补字或省略中间内容。
- 只摘与目标人物有关的内容。别人的性格不摘。
- 一时的情绪、只出现一次且没有分量的小事不摘；能反映长期倾向、价值取舍或重要经历的才摘。
- `note` 用一句话说明这句话体现了他的什么，具体、平实，不写分数和心理学术语。
- 本段没有可用的内容时返回空数组，这很常见。宁缺毋滥，每段一般不超过 8 条。
- 如果给了 `focus`，这是针对某一个方面的补查：**只摘**与 `focus` 所说方面有关的言行（`construct` 填 `trait`，`facet` 填 `focus.facets` 里的 ID），其他都不摘。

只输出一个 JSON 对象：

```json
{"evidence": [{"quote": "", "note": "", "construct": "value", "facet": null}]}
```
