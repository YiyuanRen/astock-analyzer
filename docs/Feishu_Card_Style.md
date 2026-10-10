# 飞书消息卡片风格参考

> 供 AI Agent 参考，用于合理美观地展示复杂结构化业务信息。不含任何业务语义。

---

## 一、卡片整体结构

```json
{
  "schema": "2.0",
  "config": {
    "wide_screen_mode": true,
    "update_multi": true
  },
  "header": {
    "title": {"tag": "plain_text", "content": "标题文字"},
    "subtitle": {"tag": "plain_text", "content": "摘要或时间戳"},
    "template": "red"
  },
  "body": {
    "elements": []
  }
}
```

**Header template 语义约定：**

| 值 | 适用场景 |
|---|---|
| `red` | 有告警/异常数据 |
| `green` | 空数据态 / 全部正常 |
| `blue` | 常规数据报告 |
| `purple` | 个人/分析类报告 |

---

## 二、组件清单与用法

### 1. `markdown` — 富文本段落

最基础的组件，支持加粗、颜色、超链接：

```json
{"tag": "markdown", "content": "这里是 **加粗文本** 和 <font color='red'>**红色数字**</font>"}
```

**内联颜色语义：**

| 语义 | 用途 | 颜色关键字 |
|---|---|---|
| 异常/超标值 | 非零数字标红加粗 | `red` |
| 底部说明文字 | 辅助说明、口径注释 | `grey` |
| 指标标题 | KPI 卡片中的指标名称 | `#666666` |
| 环比上升（正向） | 变化量文字 | `green` |
| 环比下降（负向） | 变化量文字 | `red` |

**规则：**
- 零值直接写 `0`，不标红
- 非零数字用 `<font color='red'>**N**</font>`（红色 + 加粗）
- 底部说明统一用 `<font color='grey'>...</font>`

---

### 2. `hr` — 分割线

```json
{"tag": "hr"}
```

用于区块之间的视觉分隔，不加额外空行。

---

### 3. `column_set` — 多列布局（核心组件）

所有横向排列的数据都用 `column_set`，不用表格 tag。

```json
{
  "tag": "column_set",
  "flex_mode": "none",
  "background_style": "grey",
  "columns": [
    {
      "tag": "column",
      "width": "weighted",
      "weight": 2,
      "vertical_align": "center",
      "elements": [{"tag": "markdown", "content": "**列名**"}]
    }
  ]
}
```

**`flex_mode` 选项：**

| 值 | 适用场景 |
|---|---|
| `none` | 手动 weighted，适合表格行 |
| `bisect` | 两列等分（KPI 2 项） |
| `trisect` | 三列等分（KPI 3 项） |
| `flow` | 自动流式（KPI 4 项以上） |

**`background_style` 选项：**

| 值 | 适用场景 |
|---|---|
| `grey` | 表格行、数据行（含表头行）的背景色 |
| `default` | 无背景（KPI 组外层） |
| `blue-50` / `green-50` / `red-50` 等 | KPI 卡片列背景（见语义色块表） |

**数据表格的标准 weight 分配：**
- 第一列（标签/名称列）：`weight: 2`
- 其余数据列：`weight: 1`

---

### 4. KPI 组件（用 `column_set` 实现）

每个 KPI 块为独立的 `column`，三行文字结构：

```
column:
  background_style: <语义色块，如 blue-50>
  padding: "8px"
  elements:
    - markdown:
        line1: <font color='#666666'>指标名称</font>
        line2: **<font color='语义色'>数值</font>**
        line3: <font color='green/red/grey'>环比变化文字</font>  （可选）
```

**语义色块与字体色对照：**

| 语义 | `background_style` | 字体色关键字 |
|---|---|---|
| 收入/交易量 | `blue-50` | `blue` |
| 利润/完成率 | `green-50` | `green` |
| 用户/流量 | `violet-50` | `violet` |
| 成本/退单 | `orange-50` | `orange` |
| 告警/异常 | `red-50` | `red` |
| 效率/转化 | `turquoise-50` | `turquoise` |
| 满意度/评分 | `yellow-50` | `yellow` |
| 其他中性 | `grey-50` | `grey` |

中性指标无法语义匹配时，从以下扩展色池中按标题哈希确定性选取：

```
blue-50 / carmine-50 / green-50 / indigo-50 / lime-50 / orange-50
purple-50 / sunflower-50 / turquoise-50 / violet-50 / wathet-50 / yellow-50
```

---

### 5. `chart` — VChart 图表

```json
{
  "tag": "chart",
  "chart_spec": {},
  "height": "280px"
}
```

**支持图表类型：** `line` / `area` / `bar`（竖） / `bar` + `"direction":"horizontal"`（横） / 分组柱状 / `pie` / 双轴

**默认调色板（7 色，多系列时顺序使用）：**

| 序号 | 色值 | 视觉色 |
|---|---|---|
| 1 | `#5A8EF9` | 蓝 |
| 2 | `#4FE8B0` | 青绿 |
| 3 | `#FAC240` | 琥珀黄 |
| 4 | `#A166FF` | 紫 |
| 5 | `#00E2FF` | 青 |
| 6 | `#FA8246` | 橙 |
| 7 | `#FFA7C1` | 粉 |

**告警类堆叠柱状图专用配色（两色）：**
- 超时/异常：`#FFD180`（蜜橙）
- 正常/未超时：`#B3E5FC`（冰蓝）
- 数字在色块内居中：`label: {visible: true, position: "inside", style: {fill: "#000000"}}`

**轴线风格（统一规范）：**
- Y 轴：无刻度线、无轴线、无 grid，标签字号 10px，颜色 `#9199A6`
- X 轴（类目）：无刻度，轴线 0.5px `#EDEDED`，标签字号 10px，颜色 `#9199A6`
- 柱体：`barMaxWidth: 16`，`cornerRadius: 4`

**图例（多系列时）：**
- 位置底部居中，间距 `spaceCol: 18`
- 图例形状：circle，size 6
- 颜色 `#5A6575`，字号 12px

**高密度数据（x 轴分类 > 15 个）：** 自动启用 DataZoom 滚动条，高度调整为 360px。

**Tooltip 面板：** 圆角 10px，背景 `rgba(255,255,255,0.95)`。

---

### 6. `collapsible_panel` — 折叠面板

```json
{
  "tag": "collapsible_panel",
  "expanded": false,
  "header": {"title": {"tag": "plain_text", "content": "点击展开明细"}},
  "elements": []
}
```

- 默认收起（`expanded: false`），避免首屏过长
- 用于：超时明细、质量变化明细等低优先级内容

---

### 7. `button` — 操作按钮

```json
{
  "tag": "button",
  "element_id": "action_button",
  "text": {"tag": "plain_text", "content": "按钮文字"},
  "type": "primary",
  "width": "fill",
  "size": "medium",
  "behaviors": [{"type": "open_url", "default_url": "https://example.com"}]
}
```

多个按钮用 `column_set` 并排（每个按钮套一个 `weight: 1` 的 column）。

`type` 选项：`primary` / `default` / `danger`，同一卡片内按钮样式保持统一。

---

## 三、数据表格的标准写法

飞书卡片不支持原生表格组件，全部用 `column_set` 行堆叠实现。

**结构层次：**

```
表头行     column_set  background_style: grey  全列加粗
汇总行     column_set  background_style: grey  汇总值加粗
分割线     hr
数据行×N   column_set  background_style: grey
```

**排序规则：** 数据行按合计降序，最大值靠左/靠上。

**Top N 标记：** 排名前 3 的项在名称列加 `❗ ` 前缀并加粗，其余普通文本。

---

## 四、区块结构与 Emoji 标题

复杂报告卡片用固定区块结构，每个区块标题带 emoji 前缀：

```
📌 概览 / 总览
🎯 关键瓶颈
📞 / 📋  跟进质量 / 数据明细
⚠️  风险提示 / 分布情况
📈 趋势变化
✅ 行动清单 / 建议
💡 洞察分析
🔎 深入查询
```

**Markdown 格式约定：**
- 区块标题：`#### 📌 概览`（H4 级别）
- 区块间用 `---` 分隔（即 `hr` 组件）
- 同一区块内不加额外空行
- 分析段落固定前缀：`**💡 分析：**`（加粗 + 冒号）
- 列表用 `•` 或 `-` 开头，序号用全角括号 `（N）`

---

## 五、信号灯 Emoji 规范

在状态文本中统一用以下三色系：

| Emoji | 语义 | 用途 |
|---|---|---|
| 🔴 | 紧急 / 制度未达标 | 需立即处理的事项 |
| 🟠 | 重要 / 风险 | 本阶段需关注的事项 |
| 🟢 | 正常 / 达标 | 良好状态确认 |

正文区块（非行动清单）通常不加 emoji，保持简洁。

---

## 六、底部固定模式

每张卡片底部固定两行（通过一个 `markdown` 组件合并输出）：

```markdown
<font color='grey'>口径说明文字，用 · 分隔各项定义</font>
[进入工作台](https://example.com/entry)
```

- 说明文字统一灰色，不加「说明：」前缀
- 链接用 markdown 超链接，不用按钮（按钮用于主操作）
- 系统版本标记放在最末：`<font color='grey'>系统名 sys@vX.X.X</font>`

---

## 七、空数据态

每种卡片都需要定义空数据态，不发空内容：

- Header template 改为 `green`
- 正文用 emoji 庆祝文案，如：`🎉 今日无异常，太棒了 👍`
- 仍需把消息发出去，只是卡片内容替换为空数据态文案

---

## 八、副标题摘要写法

副标题用于"一句话总结"，格式固定：

```
全量共<font color='red'>**N**</font>个异常，涉及<font color='red'>**X**</font>个分组，
<font color='red'>**A、B、C**</font>为前三重点关注
```

- 关键数字和名称：红色加粗
- 中文标点，句式简洁，不超过两行

---

## 九、日期与时间格式

| 场景 | 格式 |
|---|---|
| 卡片标题（带日期） | `M月D日 · 报告类型名称`（不 zero-pad） |
| 副标题/生成时间 | `YYYY年MM月DD日 HH:MM:SS` |
| 日报副标题 | `生成时间：YYYY-MM-DD HH:mm` |

---

## 十、完整 JSON 骨架示例

以下为一张包含表格 + 底部说明的标准数据报告卡片结构：

```json
{
  "schema": "2.0",
  "config": {"update_multi": true, "wide_screen_mode": true},
  "header": {
    "title": {"tag": "plain_text", "content": "4月28日 · 数据报告"},
    "subtitle": {"tag": "plain_text", "content": "全量共396个异常，涉及8个分组，A、B、C需要重点关注"},
    "template": "red"
  },
  "body": {
    "elements": [
      {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "grey",
        "columns": [
          {"tag": "column", "width": "weighted", "weight": 2, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "**分组**"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "**合计**"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "**类型A**"}]}
        ]
      },
      {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "grey",
        "columns": [
          {"tag": "column", "width": "weighted", "weight": 2, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "**汇总**"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "<font color='red'>**396**</font>"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "0"}]}
        ]
      },
      {"tag": "hr"},
      {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "grey",
        "columns": [
          {"tag": "column", "width": "weighted", "weight": 2, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "❗ **分组A**"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "<font color='red'>**100**</font>"}]},
          {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "center",
           "elements": [{"tag": "markdown", "content": "0"}]}
        ]
      },
      {"tag": "hr"},
      {
        "tag": "markdown",
        "content": "<font color='grey'>类型A说明 · 类型B说明</font>\n[进入工作台](https://example.com/entry)"
      }
    ]
  }
}
```
