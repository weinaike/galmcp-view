"""Course placement survey: question spec + scoring (single source of truth).

Consumed by app.py (the /survey routes) and the survey templates, so the
questions live in exactly one place. The survey is a behaviour-anchored
self-assessment organised as two progressive layers plus a short "extra"
block:

  第一层 · 代码能力与工具   → tech axis (coding ability)
  第二层 · AI 使用深度      → llm axis (depth-of-AI-use ladder)
  补充信息                   → resources + motivation (context, not scored)

The two scored axes map to a tier with syllabus-adjustment suggestions.

Question shape:
  id             form field key (q1..q7)
  type           'single' (radio) | 'multi' (checkbox) | 'text' (textarea)
  section        'layer1' | 'layer2' | 'extra' — drives the form's layer headers
  prompt         question text
  hint           optional sub-text (rendered muted under the prompt)
  options        list of {key, label} (single / multi only)
  other_text     (multi only) if True, render a free-text "其他" input whose
                 value is stored under "<id>_other" alongside the option list
                 (Q6). Checked options are unaffected.
  exclusive_none (multi only) the key of a "none of these" option that is
                 mutually exclusive with the rest (Q2 / Q5)
  maxlength, placeholder (text only) textarea config
Single questions are always required; multi default to required; text default
to optional (set required: True to require one).
"""

SECTIONS = {
    'layer1': {'title': '第一层 · 代码能力与工具',
               'subtitle': '先了解你的编码能力和平时用的工具'},
    'layer2': {'title': '第二层 · AI 使用深度',
               'subtitle': '再看你用到 AI 的程度'},
    'extra': {'title': '补充信息',
              'subtitle': '帮我们定课程节奏和实操方式'},
}

SURVEY = [
    {
        'id': 'q1',
        'type': 'single',
        'section': 'layer1',
        'prompt': '你的写代码能力？',
        'options': [
            {'key': 'a', 'label': '不会写代码'},
            {'key': 'b', 'label': '能看懂、会改现成脚本'},
            {'key': 'c', 'label': '能独立写程序（Python / JS / Java 等）'},
            {'key': 'd', 'label': '做过后端 / API / 工程化项目，或调过各种 SDK'},
        ],
    },
    {
        'id': 'q2',
        'type': 'multi',
        'section': 'layer1',
        'prompt': '你平时用什么写代码？（多选）',
        'hint': '了解你常用的编码环境，用于安排实操方式。',
        'options': [
            {'key': 'terminal', 'label': '终端 / 命令行（CLI）'},
            {'key': 'ide', 'label': '编辑器或 IDE（VSCode、PyCharm 等）'},
            {'key': 'notebook', 'label': 'Jupyter Notebook'},
            {'key': 'online', 'label': '在线平台（Google Colab 等）'},
            {'key': 'none', 'label': '以上都没怎么用过'},
        ],
        'exclusive_none': 'none',
    },
    {
        'id': 'q3',
        'type': 'single',
        'section': 'layer2',
        'prompt': '你用到 AI 的程度，最接近哪一档？',
        'hint': '选你真实达到的最深一档，不必贪高。',
        'options': [
            {'key': 'a', 'label': '基本只是和 AI 对话（聊天、问问题、让它写写文字）'},
            {'key': 'b', 'label': '用过编程 Agent（如 Claude Code、Cursor）帮我写 / 改代码'},
            {'key': 'c', 'label': '搭过智能体或 AI 应用（Coze、Dify、扣子，或自己写程序调 API 配工具）'},
            {'key': 'd', 'label': '做过更深的机制设计 / 决策优化（Agent 架构、多 Agent 编排、prompt 与工具链的深度调优）'},
        ],
    },
    {
        'id': 'q4',
        'type': 'multi',
        'section': 'layer2',
        'prompt': '除了普通对话，你还用过 AI 的哪些玩法？（多选）',
        'hint': '了解你驱动 AI 的熟练程度，用于安排 prompt 工程等章节。',
        'options': [
            {'key': 'multimodal', 'label': '传过图片 / 文件给 AI（多模态）'},
            {'key': 'search', 'label': '用过联网 / 搜索增强（如 ChatGPT 联网、Perplexity）'},
            {'key': 'prompt', 'label': '自己写过 system prompt / 设定角色 / few-shot 引导'},
            {'key': 'param', 'label': '调过参数（温度、top-p 等）'},
            {'key': 'none', 'label': '以上都没用过，只用普通文字对话'},
        ],
        'exclusive_none': 'none',
    },
    {
        'id': 'q5',
        'type': 'multi',
        'section': 'extra',
        'prompt': '你目前能用上下面哪些？（多选）',
        'hint': '这决定课程实操部分能不能跑起来。',
        'options': [
            {'key': 'apikey', 'label': '有可用的 Claude / OpenAI 等 API key'},
            {'key': 'runtime', 'label': '本机能跑 Python 或 Node.js'},
            {'key': 'cc', 'label': '装过 / 用过 Claude Code、Cursor 这类 AI 编程工具'},
            {'key': 'paid', 'label': '已有/愿意AI Token订阅'},
            {'key': 'none', 'label': '以上都没有'},
        ],
        'exclusive_none': 'none',
    },
    {
        'id': 'q6',
        'type': 'multi',
        'section': 'extra',
        'prompt': '你来上这门课，主要为了？（多选）',
        'other_text': True,
        'options': [
            {'key': 'app', 'label': '工作里要搭 AI 应用 / 智能体'},
            {'key': 'cc', 'label': '想用好或扩展 Claude Code 这类编程 Agent'},
            {'key': 'rag', 'label': '做知识库 / 检索问答（RAG）系统'},
            {'key': 'interest', 'label': '纯兴趣 / 想了解前沿'},
        ],
    },
    {
        'id': 'q7',
        'type': 'text',
        'section': 'extra',
        'prompt': '你最想学的是什么？你想用它来做什么？',
        'required': False,
        'maxlength': 500,
        'placeholder': '（选填）比如：想学会自己搭一个能查文献的 Agent；想让 AI 帮我批量处理数据……',
    },
]


# --- scoring ---

# Layer 1 (q1 coding ability) → tech axis; Layer 2 (q3 AI-depth ladder) → llm axis.
_Q1_SCORE = {'a': 0, 'b': 1, 'c': 2, 'd': 3}   # coding ability
_Q3_LLM = {'a': 0, 'b': 1, 'c': 2, 'd': 3}     # AI depth ladder


TIERS = {
    'newbie': {
        'label': '新手 / 0 基础',
        'color': 'orange',
        'blurb': '你刚接触 AI 工具，技术背景也较弱。课程对你应该是从零开始的入门路径。',
        'suggestions': [
            '先补“LLM 是什么、能做什么”——开头的基础章节对你最关键，别跳。',
            '降低动手门槛：每章配可复制粘贴的示例和截图，先跟做再理解。',
            'Agent / MCP / Skill 这几章放慢讲，多用类比，少假设工程背景。',
            '实操环节尽量用托管环境，别让你卡在装环境上被劝退。',
        ],
    },
    'user': {
        'label': '有使用经验 / 半技术',
        'color': 'accent',
        'blurb': '你已经在日常用 AI，也有一定技术底子。课程可以正常推进，重点是把“会用”升级成“会搭”。',
        'suggestions': [
            '基础部分可正常过，但把重心放在 prompt 工程规范、工具声明、Agent 架构上。',
            '实操给足脚手架和示例，帮你从“调用”走到“组装”。',
            '留意 system prompt / few-shot / function calling 的动手练习，这是你的增长点。',
        ],
    },
    'advanced': {
        'label': '开发者 / 进阶',
        'color': 'green',
        'blurb': '你有开发背景、也深入用过 AI 应用。课程对你应该是加速 + 深挖架构。',
        'suggestions': [
            '基础部分可以压缩或快进，把课时让给 MCP / Skill / 上下文管理这些进阶主题。',
            '加重动手工程：自己写一个 MCP server（fastmcp）、扩展 Claude Code、设计 Skill。',
            '关注架构权衡（MCP vs Skill、DAG vs Agent 循环），而不是工具用法本身。',
            '可作为“进阶小队”，带扩展/挑战任务。',
        ],
    },
    'dev_newbie': {
        'label': '特殊组合：强开发 + LLM 新手',
        'color': 'accent',
        'blurb': '你写代码很熟，但对 LLM 的概念和生态是新的。技术不是你的瓶颈，概念才是。',
        'suggestions': [
            'LLM 概念部分（token、prompt 角色、上下文）从头讲，但可以快进——你吸收技术细节很快。',
            '技术/工程细节（API、SDK、fastmcp、CLI）可以讲深，这是你的主场。',
            '别因为“会写代码”就跳过概念：概念题答错正说明概念是新的，先补齐。',
            '适合当“技术带头人”，但先帮他把 LLM 心智模型建立起来。',
        ],
    },
}

# display order for the admin results page
TIER_ORDER = ['newbie', 'user', 'advanced', 'dev_newbie']


def score(answers):
    """Map a respondent's answers to a tier + display info.

    ``answers`` is {q1: 'a'..'d', q3: 'a'..'d', q2/q4/q5/q6: [keys]} (multi
    values are lists). Only Layer 1 (q1 coding ability → tech) and Layer 2's
    depth ladder (q3 → llm) are scored; q2/q4/q5/q6 are context. Returns a dict
    with tier key/label/color/blurb, the two axis scores, and the
    syllabus-adjustment suggestions.
    """
    q1 = answers.get('q1') or ''
    q3 = answers.get('q3') or ''

    tech = _Q1_SCORE.get(q1, 0)          # Layer 1: coding ability
    llm = _Q3_LLM.get(q3, 0)             # Layer 2: AI depth ladder

    # priority ladder
    if llm <= 1 and tech >= 2:
        tier = 'dev_newbie'
    elif llm <= 1:
        tier = 'newbie'
    elif llm == 2:
        tier = 'user'
    elif llm >= 3 and tech >= 2:
        tier = 'advanced'
    else:                       # llm >= 3 but tech < 2
        tier = 'user'

    info = TIERS[tier]
    return {
        'tier': tier,
        'tier_label': info['label'],
        'tier_color': info['color'],
        'tier_blurb': info['blurb'],
        'llm': llm,
        'tech': tech,
        'suggestions': info['suggestions'],
    }
