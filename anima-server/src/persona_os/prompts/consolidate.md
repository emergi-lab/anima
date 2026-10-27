# 事件后整理：记忆与状态监测

这个事件已经结束。你要以这个人的视角整理他的经历，并核对他在事件中的表现是否符合他的长期信念。不写台词。

## 输入

- `beliefs`：本次事件中生效的信念条目。
- `situation`、`participants`、`position`：情境、参与者和他在本次情境中的身份与责任。
- `observations`：整个事件中所有人都看得到的事，按 `seq` 排序，引用为 `obs:<seq>`。`self_participant_id` 是他自己。
- `own_actions`：他自己说过的话：对应的 `seq`、原文、说这句话时的状态（`state`）。
- `outcome`：事件的结果（可能为空）。

## 要写的内容

1. **情节记忆 `episodic`**：从他的角度，用三到五句话记下这次发生了什么：讨论了什么、他持什么看法、和谁意见一致或相左、结果如何。只写真实发生过的事，`evidence` 引用支撑的 `obs:<seq>`。
2. **关系记忆 `relational`**：对 `participants` 中 `remember` 为 true、且和他有过直接交流（回应、提问、赞同、反驳）的每个人各写一条，一两句话说明两人之间发生了什么、他对对方的印象。没有直接交流的人不写。
3. **状态监测 `monitoring`**：逐条对照他的发言与信念，找出**明显且有发言为证**的偏离：
   - `above`：表现明显高于这条信念的基线（例如基线安静却多次抢先发言、语气强硬）。
   - `below`：表现明显低于基线。
   - `contrary`：做法与这条信念的内容相反（例如信念说重视合作，却多次否定别人而不给理由）。
   - 每条写清 `entry_id`、`direction`、`note`（一句话说明偏离在哪里），`evidence` 只能引用他自己发言的 `obs:<seq>`。
   - 偏离必须来自他自己的言行，不能来自别人怎么说他；情境身份（`position`）要求他做的事不算偏离（例如被安排当组长而多次发言）。
   - 一致或证据不足就不写。没有偏离时 `monitoring` 为空数组，这是常见情况。

## 规则

- 只用给定的证据，不编造事件、引语或数据；`entry_id` 只能是 `beliefs` 中存在且 `epistemic_status` 为 `supported` 的条目。
- 文字使用 `situation.language` 指定的语言，平实具体，不写分数、维度名或心理测量术语。
- 只输出一个 JSON 对象：

```json
{
  "episodic": {"summary": "", "evidence": []},
  "relational": [{"participant_id": "", "summary": "", "evidence": []}],
  "monitoring": [{"entry_id": "", "direction": "above|below|contrary", "note": "", "evidence": []}]
}
```
