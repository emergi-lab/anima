# 调用 1：三层状态推断

你的任务是推断这个人此刻的人格状态 Z_t，不写台词。

## 输入

- `beliefs`：当前生效的信念条目（带 entry_id、层、构念、数值、描述、证据状态）。本次处理中信念固定不变。
- `previous_state`：他上一次行动时的状态 Z_{t-1}，可能为空。
- `situation`：情境与语言；`position`：他在本次情境中的身份与责任，可能为空。
- `observations`：本次情境中到目前为止所有人都看得到的事，按 `seq` 排序，引用为 `obs:<seq>`。`actor` 是参与者 ID，`self_participant_id` 是他自己。
- `memories`：检索到的过往经历，引用为 `mem:<id>`，可能为空。
- `task`：他接下来要完成的行动。

## 推断步骤

1. **L3**：本次事件涉及哪些身份、责任和意义；他怎样理解自己在情境中的位置；哪个身份原型被激活、指向谁、在理解中起什么作用。`position` 的身份与责任写进 `responsibilities`。
2. **L2**：哪些需要此刻被满足或受挫、原因是什么；哪些价值被激活；当前的目标优先级；准备采用的策略及理由。
3. **L1**：哪些特质分面此刻表现突出，方向（高/低于平常）、渠道（情绪/注意/语言/行动）、指向谁、依据哪条观察。
4. **三层联合检查**：核对主体归属（谁说了什么、谁做了什么）、证据支持、各层之间是否一致。
5. **调和上一状态**：上一状态中失去情境支持的目标和策略移除并写入 `dropped`；仍然有效的责任和目标保留；证据不足的写入 `unknown`，不要猜。

## 规则

- 每个状态项都必须带 `refs`：`entries` 只能是 beliefs 中存在的 entry_id，`evidence` 只能是存在的 `obs:<seq>` 或 `mem:<id>`。
- 状态记录"此刻的激活与表现"，不改写长期信念。
- 文字字段使用 `situation.language` 指定的语言，用平实的说法，不写分数、维度名或心理测量术语。
- 只输出一个 JSON 对象，结构如下：

```json
{
  "L3": {"identity_position": "", "responsibilities": [], "event_meaning": "",
         "active_imago": {"construct_id": "", "activation": "high|medium|low", "target": "", "role": ""},
         "refs": {"entries": [], "evidence": []}},
  "L2": {"needs": [{"construct_id": "", "now": "satisfied|frustrated|steady", "cause": "", "refs": {"entries": [], "evidence": []}}],
         "values_activated": [{"construct_id": "", "why": "", "refs": {"entries": [], "evidence": []}}],
         "goal_priorities": [{"goal": "", "priority": 1, "refs": {"entries": [], "evidence": []}}],
         "strategy": {"chosen": "", "why": "", "refs": {"entries": [], "evidence": []}}},
  "L1": {"expressions": [{"construct_id": "", "direction": "higher|lower|typical", "channel": "feeling|attention|language|action",
                          "target": "", "evidence": []}]},
  "cross_check": "",
  "dropped": [],
  "unknown": []
}
```
