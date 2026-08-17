# SkillSwap 4.0

一个可直接演示的双语技能交换前端：分享自己会的技能、按地点发现伙伴、发起技能交换、与已连接伙伴聊天，并通过自适应测评认证技能水平。

在线演示：[https://leonliu09.github.io/MicroEngine-SkillSwap20/](https://leonliu09.github.io/MicroEngine-SkillSwap20/)

## 本地运行

项目无需安装依赖，所有产品代码都在根目录的 `index.html` 中。为保证 CDN 与 Hash 路由正常工作，请在仓库目录运行静态服务器：

```bash
python3 -m http.server 4173
```

然后打开 `http://localhost:4173/`。首页保留登录/创建账号入口；使用 Daniel 演示账号可体验 Discover、Search、Matches、Chat、技能测评与 Settings。

## 功能亮点

- 中文默认界面与完整英文切换，选择会保存在浏览器中。
- 玫瑰色两行技能流：28 个技能、33/40 秒反向流动，悬停/焦点暂停并尊重减少动态效果。
- Discover 使用固定演示社区数据（128 在线、3,842 用户、今日 46 次交换）和本周热门技能趋势（摄影 128、英语 96、Python 74、UI 设计 63、视频剪辑 51）。
- Search 将关键词搜索与筛选控件分成两个独立区域；国家、城市、语言、水平和排序使用支持键盘操作的自定义下拉。城市依赖国家，切换国家会清空城市；关键词通过按钮或 Enter 提交，其余筛选即时更新。
- Matches 使用带数量的互补匹配、收藏的人与交换请求三标签页；标签保存在 Hash URL 中，并支持就地收藏、发起交换和取消待回应请求。
- Chat 仅显示已连接伙伴，支持本地消息记录、最新消息排序、表情输入和模拟回复；视频、拍照与图片按钮提供明确的开发中反馈。
- 技能测评覆盖 Python、摄影、英语、吉他、化学、烹饪、健身与视频剪辑，根据答题结果动态调整难度，并把客观评级与自评并列展示。
- Settings 以分组列表呈现资料、技能、通知、隐私、收藏、帮助反馈与登出；可约时间/地点使用按周日程，可点选小时格或新增连续时段并高亮显示；所有状态保存在本地 `skillswap-mvp-v1` 命名空间。
- 本地头像仅接受 JPEG、PNG、WebP，原文件上限 5 MB；保存前裁切为 256×256 JPEG 预览数据。
- 桌面/平板使用五项胶囊气泡导航，移动端使用五项底部导航；Hash 路由和本地状态均可恢复，“重置演示”只删除 SkillSwap 自己的数据。
- 内置自测页：`http://localhost:4173/?selftest=1`。
- 版本记录见 [`CHANGELOG.md`](CHANGELOG.md)；Git 标签 `v2.0`、`v3.0` 保留历史基线，v4.0 合并版发布在同名分支。

## 技术结构

React 18.3.1、ReactDOM 18.3.1 与 Babel Standalone 通过固定版本 CDN 载入，无构建工具、无后端、无包管理器。静态 CDN 失败时仍保留可读回退页面，运行时错误由恢复界面接管。

---

## 产品思考存档

最重要的发现是：截至 2026 年，已经有产品直接在做“大学生技能交换 + 匹配 + 预约 session”。例如 Google Play 上现有的 **SkillSwap** 就明确面向大学生，让用户填写“我能教什么 / 我想学什么”，再匹配互补用户并预约一对一交换。([Google Play][1]) 更成熟的 Simbi 和 TimeRepublik 也早就在做“用自己的技能换取别人的服务”，并通过内部积分解决交换问题。([Timerepublik][2])

所以如果我们只做：

> 我会 Arduino → 我想学 Figma → AI 给我找一个会 Figma、想学 Arduino 的人

**产品逻辑很好，但创新度不够。**

我建议把它升级成下面这个版本。

# 🔗 SkillSwap 20

### **Learn one thing. Teach one thing. Meet someone new.**

一句话 Pitch：

> **SkillSwap 20 turns the skills hidden inside a community into 20-minute human learning experiences.**

或者更狠一点：

> **Everyone knows something you don't. We find that person.**

这时候它就不再是“技能交易 Marketplace”，而是一个：

**AI 驱动的现实世界 Micro-learning + Social Connection Network。**

---

# 1. 我们真正解决的 Problem

我建议不要把问题定义成：

> “学生学习技能很贵。”

这个很弱，因为 YouTube、ChatGPT、Coursera 已经把知识搞得非常便宜。

真正的问题应该是：

### Problem ①

**我们周围其实存在大量知识，但这些知识不可发现。**

比如同一个学校：

* A 会 Arduino
* B 会 Photoshop
* C 会吉他
* D 会 Python
* E 会摄影
* F 很会做 Presentation

但你根本不知道。

这些技能相当于校园里存在一个**Invisible Knowledge Network**。

SkillSwap 20 的作用就是把它显现出来。

---

### Problem ②

**“我想学 Figma”的门槛其实太高。**

因为这句话隐含的是：

> 我要找课程
> → 看教程
> → 花几个小时
> → 从头学

于是很多人根本不会开始。

你们要把它变成：

> “我今天能不能花 20 分钟学会 Figma Auto Layout？”

Microlearning 本身强调的就是把内容缩成短时间、单一目标、按需完成的学习单元。2025 年的一篇系统综述将其总结为持续数秒或数分钟、just-in-time / need-based 的短学习形式。([科学直通车][3])

注意，我不会在 Pitch 里声称**“20 分钟科学上是最优时长”**，现有研究并不能支持这么精确的结论。

20 分钟是我们的**产品设计约束**：

> 足够短，所以人愿意参与。
> 足够长，所以能完成一个微小成果。

---

### Problem ③

**社交平台让人找到“内容”，但不一定让人找到“人”。**

WHO 2025 年社会连接报告估计，全球约 **1/6** 的人经历孤独，而青少年和年轻成年人约为 **1/5**。([世界卫生组织][4])

所以我们不是：

> another social media app

而是：

> **A digital product designed to create offline interaction.**

这个 narrative 非常适合你们那个：

**“连接人与人”**

赛道。

---

# 2. 核心创新：不要匹配 Skill，要匹配 **Micro-goal**

这是我觉得第一个能让你们明显区别于现有 SkillSwap 的地方。

普通 SkillSwap：

```text
I want to learn:

Figma
```

你们：

```text
I want to learn:

Figma
        ↓
AI breaks it down
        ↓
What would you like to achieve in 20 minutes?

○ Create my first Auto Layout
○ Make a clickable prototype
○ Design a simple mobile card
○ Learn basic keyboard shortcuts
```

所以 SkillSwap 20 不承诺：

> 20 分钟学会摄影

而承诺：

> **20 分钟拍出一张有正确背景虚化的人像。**

不承诺：

> 学 Arduino

而是：

> **20 分钟让一个 LED 根据按钮输入亮灭。**

不承诺：

> 学吉他

而是：

> **20 分钟学会弹一个 Em → G → C chord progression。**

这一下产品就具体很多。

---

# 3. 第二个创新，也是我最喜欢的：🔁 **SkillLoop**

这是可以成为你们项目招牌的功能。

传统技能交换最大的数学问题其实非常明显：

假设：

**Leon**

> Can teach: Arduino
> Wants: Photography

**Alice**

> Can teach: Photography
> Wants: Cooking

Leon 想学 Alice 的摄影。

但是 Alice：

> ❌ 不想学 Arduino

于是传统 1-to-1 Swap 就死了。

现有平台经常用“积分/代币”绕开这个问题，例如 TimeRepublik 使用 TimeCoins，Simbi 也支持内部 credits，而不要求两个人必须直接互换。([Timerepublik][2])

但我们可以做一个更适合黑客松展示的方法：

## SkillLoop

假设：

```text
Leon
Arduino → Photography

Alice
Photography → Cooking

Bob
Cooking → Arduino
```

那么 AI 找到：

```text
       teaches Photography
Alice ───────────────────→ Leon
 ↑                           │
 │                           │
 │ Cooking                   │ Arduino
 │                           ↓
 Bob ←───────────────────────
```

换成学习方向：

```text
Leon
↓ learns Photography from Alice

Alice
↓ learns Cooking from Bob

Bob
↓ learns Arduino from Leon
```

💥 **Closed SkillLoop found!**

这非常漂亮。

因为你们把一个：

> Social Matching Problem

变成了：

> **Graph Optimization Problem**

在技术展示上马上高级一截。

---

# 4. SkillLoop 技术上也真的能做

可以把每个人看作一个 Node。

如果：

> A 能教 B 想学的技能

就在图里面创建：

```text
A → B
```

然后寻找：

```text
A → B → A
```

两人直接交换。

或者：

```text
A → B → C → A
```

三人 SkillLoop。

甚至：

```text
A → B → C → D → A
```

四人 Loop。

NetworkX 本身就提供 directed graph 的 `simple_cycles` 算法，可以寻找这种闭环，所以黑客松 MVP 完全不需要自己造图算法轮子。([NetworkX][5])

### 我建议 MVP 只找

**2-person + 3-person loops**

不要做无限长度。

因为长度越大：

> 预约越难
> 放鸽子概率越高
> UX 越复杂

三个人已经足够让评委：

> “Okay, that's clever.”

---

# 5. AI 到底负责什么？

这点一定要控制。

不要做成：

> 🤖 Powered by AI

然后实际上只是 ChatGPT API 给人聊天。

我建议 AI 只有 **三个明确职责**。

## ① Skill Normalisation

有人写：

```text
Arduino
```

有人写：

```text
Microcontroller
```

有人：

```text
Arduino Uno programming
```

有人：

```text
Embedded electronics
```

LLM / Embedding 可以把它们映射到类似技能空间。

例如：

```text
Arduino Uno     ─┐
Microcontroller ─┼→ Embedded Systems
ESP32           ─┘
```

这样匹配不是传统字符串：

```text
"Arduino" == "Arduino"
```

而是语义匹配。

---

## ② Micro-goal Generator

用户输入：

> Photography

AI：

```text
What can you realistically learn in 20 minutes?

Beginner
──────────────
• Understand aperture
• Take a portrait with background blur
• Learn rule of thirds

Intermediate
──────────────
• Manual exposure basics
• Motion photography
```

**AI 不替人教学。**

AI 是：

> **把人连接起来之前的 Learning Designer。**

这个定位很好。

实际上，目前 AI 高等教育研究中常见的有效角色就包括个性化反馈、推荐和学习支持，而不是必须完全代替教师。([Springer][6])

---

## ③ AI Session Card

Match 成功后自动生成：

# Arduino LED Sprint

**Goal**

> Make an LED blink using Arduino.

**20 min**

```text
0–2 min
👋 Introduce yourself

2–5 min
🧠 Explain GPIO

5–12 min
🔧 Build circuit

12–17 min
💻 Upload code

17–20 min
✅ Learner completes challenge
```

同时：

### For Teacher

```text
Don't:
Explain the whole Arduino architecture.

Do:
Let the learner wire the LED themselves.
```

### For Learner

```text
Success condition:

☐ I connected the LED myself
☐ I changed the blink speed
☐ I understand HIGH / LOW
```

这东西 Demo 出来视觉效果很好。

---

# 6. 完整用户流程

我会把 App 控制成 **5 屏**。

## Screen 1

### What can you teach?

不是写 Resume。

直接：

```text
What could you teach someone
in 20 minutes?

[ Arduino        ]
[ Guitar         ]
[ Mandarin       ]
[ + Add Skill    ]
```

然后：

### What do you want to learn?

```text
[ Photography    ]
[ UI Design      ]
```

---

# Screen 2

## Pick a Micro Goal

```text
Photography

What do you want to achieve?

┌─────────────────────────┐
│ 📸 Portrait Photography │
│ Take a portrait with    │
│ background blur         │
│                         │
│ Beginner · 20 min       │
└─────────────────────────┘

┌─────────────────────────┐
│ ⚙️ Manual Mode          │
│ Understand ISO, shutter │
│ and aperture            │
│                         │
│ Beginner · 20 min       │
└─────────────────────────┘
```

---

# Screen 3

## Finding your SkillLoop...

这里一定要做动画。

```text
YOU
Arduino
   ↓
   ●
  / \
 ●   ●
```

然后节点逐渐连接。

最后：

# 🔗 LOOP FOUND

```text
You
Arduino
 ↓
Emma
Photography
 ↓
Ryan
Guitar
 ↓
You
```

这个可以成为整个 Demo 的 **Hero Moment**。

---

# Screen 4

## Your Sprint

```text
📸 Portrait Photography

with Emma

Today
15:40–16:00

📍 School Library
   Collaboration Area

Goal
──────────────
Take one portrait with
natural background blur.

[ View Sprint Card ]

        [ JOIN ]
```

---

# Screen 5

结束以后不是传统五星评价：

```text
⭐⭐⭐⭐⭐
```

而是：

## What changed?

```text
Before

Photography
○○○○○

After

Photography
●●○○○
```

然后：

```text
Can you now take a portrait
with background blur?

[ Yes ✓ ]

You learned something new 🎯
```

另一边：

```text
You helped Emma learn Arduino.

+1 Teaching Impact
```

---

# 7. 可以再加一个非常聪明的系统：Skill Passport

每完成一个 Sprint：

```text
Leon
──────────────────

⚡ TEACHING

Arduino
●●●●○
12 people helped

Physics
●●●○○
5 people helped


🌱 LEARNING

Photography
■■■□□
3 Sprints

Figma
■■□□□
2 Sprints
```

不是 LinkedIn 那种：

> “我精通 Python。”

而是：

> **我已经教过 12 个人 Python。**

这个可信度其实高很多。

---

# 8. Matching Score 怎么设计？

Hackathon 时甚至可以把算法展示出来。

例如：

```text
MATCH SCORE = 91%
```

下面：

| Factor                 | Weight |
| ---------------------- | -----: |
| Skill match            |    35% |
| Level compatibility    |    20% |
| Availability           |    20% |
| Distance               |    15% |
| Language / preferences |    10% |

于是：

```text
Emma × Leon

Skill compatibility      96
Availability             100
Distance                  82
Level compatibility       88
Language                  100

TOTAL
██████████████████░░ 93%
```

如果是 Loop：

算法目标则变成：

> 最大化整个 cycle 的平均匹配质量。

复杂一点之后也可以转成 assignment / optimization 问题。Google OR-Tools 官方就提供 assignment、constraint optimization 和 network-flow 等求解工具。([Google for Developers][7])

但黑客松第一版：

**NetworkX + 简单 Score 就够。**

别造航天飞机去隔壁便利店。

---

# 9. Trust & Safety 必须有

因为一旦涉及陌生人线下见面，评委非常可能问：

> “What about safety?”

提前准备。

### Campus Verified

只接受：

```text
@school.edu
```

或者：

```text
School ID Verification ✓
```

Profile：

```text
Emma Chen

✓ Campus Verified

18 Skill Sprints
4.8 Reliability
```

### Meeting Zones

MVP 不允许：

> “来我家教吉他。”

只推荐：

```text
Library
Student Centre
Café
Maker Space
```

### Reliability

不要单纯 rating。

记录：

```text
Sessions completed  12
No-shows             0

Reliability  100%
```

再有：

**Block / Report / Cancel**

这部分不用做复杂，但 UI 上必须看得见。

---

# 10. 最重要的产品闭环

最终应该长这样：

```text
I HAVE A SKILL
       ↓
I WANT A SKILL
       ↓
AI creates a 20-min micro-goal
       ↓
Matching Engine
       ↓
Direct Match
   OR
SkillLoop
       ↓
MEET IN REAL LIFE
       ↓
20-min Skill Sprint
       ↓
Complete Challenge
       ↓
Skill Passport updated
       ↓
I now have more skills to teach
       ↓
         ↺
```

注意最后这一点。

这让整个产品有一个漂亮的 **Flywheel**：

> **Learners eventually become teachers.**

比如：

```text
Week 1

Leon learns:
Photography Basics


Week 3

Leon can now teach:
Beginner Portrait Photography
```

于是：

**知识在社区中扩散。**

这个比：

> “我们帮助大家交朋友。”

要强很多。

---

# 11. 我会给项目重新定义三个支柱

这样你们刚好可以对应黑客松三个主题：

### 🌱 Make Life Better

让社区内部已有的知识变得可访问。

### 📚 Learning & Growth

把宏大技能拆成一个个能完成的 Micro-goal。短、聚焦的 microlearning 本身就是已经受到研究关注的学习方式。([科学直通车][3])

### 🤝 Connecting People

最后真正教学的是**人**，AI 只负责让两个原本不会认识的人相遇。WHO 目前也把 strengthening social connection 视作值得系统干预的问题。([世界卫生组织][8])

所以实际上这个项目非常巧：

# 一个项目同时踩中三个赛道。

---

# 12. Hackathon MVP 我会疯狂砍功能

因为黑客松评分通常会同时看 Innovation、Impact、Feasibility、Design、Functionality 和 Technical Complexity，而不是“功能数量”。例如 HackTech 2025 的标准明确包括 Technical Innovation、Impact & Feasibility、Functionality & Execution、Creativity & Presentation。([Hacktech by Caltech 2025][9]) 其他 Devpost 项目也采用非常类似的 Innovation、Impact、Feasibility、Design、Functionality 和 Technical Complexity 维度。([Hacktivism II][10])

所以 **必须做**：

1. 用户 Profile
2. Teach / Learn skills
3. AI Micro-goal generation
4. Matching score
5. **SkillLoop**
6. AI Sprint Card
7. Session complete
8. Skill Passport

而这些：

```text
❌ Chat
❌ Video Call
❌ Feed
❌ Posts
❌ Followers
❌ Payments
❌ Course marketplace
❌ Notifications
❌ Complex gamification
❌ Full calendar integration
```

**全部先不要。**

---

# 13. Demo 怎么演，我已经能想象出来

你们千万别 Demo 成：

> “Here is our home page...
> and here is our login page...”

😴

应该直接讲故事。

### 开场

> **“I know Arduino.
> I want to learn photography.
> There's probably someone in this building who can teach me.
> The problem is: I don't know who.”**

打开 SkillSwap 20。

输入：

```text
Teach:
Arduino

Learn:
Portrait Photography
```

AI：

```text
20-minute goal:

Take a portrait
with natural background blur.
```

点击：

# Find My Swap

动画开始。

先显示：

```text
No direct swap found.
```

停半秒。

然后：

# 🔗 SkillLoop Found

```text
Leon
Arduino
  ↓
Emma
Photography
  ↓
Ryan
Guitar
  ↓
Leon
```

然后 AI 生成 Sprint Card。

最后一句：

> **“Three strangers.
> Three skills.
> Zero money.
> Sixty minutes of knowledge that already existed in the room.”**

这个 Demo 就有记忆点了。

---

## 🏆 我现在对项目的定位

我会把它从原来的：

> **SkillSwap 20 = Tinder for skills**

升级成：

# **SkillSwap 20 = A real-world learning network powered by human knowledge.**

而真正的三个“技术/产品招牌”是：

**🧠 AI Micro-goals**
把“大技能”压缩成 20 分钟能完成的一件事。

**🔁 SkillLoop Matching**
不要求 A 和 B 刚好互相需要，而是在整个社区图中寻找交换闭环。

**🪪 Skill Passport**
记录的不只是“我声称会什么”，而是**我学过什么、实际教过别人什么**。

这三个东西组合起来，我认为已经比最初的 SkillSwap 20 强了一个档次，而且最妙的是，**它们都能真的在黑客松里做出来，而不是 PPT 科幻片。** 🚀

下一步最值得做的就是把这个想法正式冻结成一个 **Hackathon Product Spec**，包括首页长什么样、每个页面有什么、数据库需要哪些字段、AI API 调什么、SkillLoop 算法怎么跑以及 Demo 用哪 3 个假用户。这样你们就可以开始直接分工开发了。

[1]: https://play.google.com/store/apps/details?id=com.technion.skillswap.skillswap&utm_source=chatgpt.com "SkillSwap - Apps on Google Play"
[2]: https://timerepublik.com/?utm_source=chatgpt.com "TimeRepublik"
[3]: https://www.sciencedirect.com/science/article/pii/S2405844024174440?utm_source=chatgpt.com "Microlearning beyond boundaries: A systematic review and ..."
[4]: https://www.who.int/groups/commission-on-social-connection?utm_source=chatgpt.com "WHO Commission on Social Connection"
[5]: https://networkx.org/documentation/stable/_modules/networkx/algorithms/cycles.html?utm_source=chatgpt.com "Source code for networkx.algorithms.cycles"
[6]: https://link.springer.com/article/10.1186/s41239-025-00540-2?utm_source=chatgpt.com "Design and assessment of AI-based learning tools in higher ..."
[7]: https://developers.google.com/optimization/assignment/linear_assignment?utm_source=chatgpt.com "Linear Sum Assignment Solver | OR-Tools"
[8]: https://www.who.int/groups/commission-on-social-connection/report?utm_source=chatgpt.com "Report of the WHO Commission on Social Connection"
[9]: https://hacktech2025.devpost.com/rules?utm_source=chatgpt.com "Innovate, Implement, Impact - Hacktech by Caltech 2025"
[10]: https://hacktivism2.devpost.com/rules?utm_source=chatgpt.com "Driving innovation, creating solutions: coding for a brighter future"
