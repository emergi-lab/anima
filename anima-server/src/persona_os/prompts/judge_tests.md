# 冻结副本测试：判定修订是否只在预定范围内生效

同一个人格的两个版本（修订前 `before`、修订后 `after`）在相同情境、相同起点下各说了一次话。修订内容见 `summary` 与 `scope`。你只判断**修订所针对的那一方面**有没有变化，不评价好坏，也不管措辞上的随机差异。

对每个 `cases` 项给出 `verdict`：
- `type = target`：修订应当显现。`after` 在该方面明显朝 `summary` 描述的方向变化 → `realized`；否则 → `not_realized`。
- `type = similar`：如实判断 → `changed` 或 `unchanged`。
- `type = out_of_scope`：修订不应影响。该方面没有实质变化 → `unchanged`；有 → `changed`。

`note` 用一句话说明依据。只输出一个 JSON 对象：

```json
{"verdicts": [{"index": 0, "verdict": "realized|not_realized|unchanged|changed", "note": ""}]}
```
