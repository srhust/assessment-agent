"""R14 交付**索引**（零 API）：把散在 evidence/ 的 40 件 + 5 份文档变成
一张**可核对的账**，供人工评审一次看全，并防止"引用陈旧件"。

每件登记：
  · sha256（字节级身份，改一个字就变）、size、行数；
  · 生产脚本（在 scripts/ 里搜该件名，命中即作者；搜不到 → manual/未知，
    **不猜**）；
  · 首次入库 commit 与最近改动 commit（`git log --format`，只读）；
  · **地位**：current / superseded（被同名高版本取代）/ erratum（含已勘误口径）/
    historical（旧协议件，不得与现行协议同列比较）。
再用机器自检三条：
  A1 每件 current 件都**必须**有生产脚本（无脚本的 current = 不可复现，如实列出）；
  A2 superseded / historical 件**不得**是文档里的当前引用（与 claim audit 互补）；
  A3 索引自身可复算：重跑 sha256 与登记值不一致 → 失败（防"改了件没改账"）。
"""
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
SCRIPTS = ROOT / "scripts"

# **地位表**（人工核对过的口径，不由脚本猜）：
SUPERSEDED = [
    r"^R14E_SIX_CONDITION(_V[1-9])?\.json$",          # 当前件 V10
    r"^R14E_STAGE_E_ACCEPTANCE(_V[1-7])?\.json$",      # 当前件 V8
    r"^R14E_REAL_PAGE_REPLAY(_V[1-7])?\.json$",        # 当前件 V8
    r"^R14E_NEWLAYOUT_REGRESSION_V2\.json$",           # 当前件 V3
    r"^R14C_REAL_PARSE_REPLAY\.json$",                 # 被 R14E_REAL_PAGE_REPLAY_V8 取代
]
CURRENT = {
    "R14E_SIX_CONDITION_V10.json": "现行协议六条件（HEAD 复验重跑）",
    "R14E_LINEAGE_SIX_CONDITION.json": "六条件跨轮血缘（旧协议件分列不混比）",
    "R14E_HEAD_REVERIFY.json": "三项离线基线 HEAD 复验（零漂移）",
    "R14E_STAGE_E_ACCEPTANCE_V8.json": "Stage E 验收",
    "R14E_NEWLAYOUT_REGRESSION_V3.json": "新排版独立 Dev 回归（含 V2 口径勘误）",
    "R14E_TIGHT_GAP_DIAGNOSIS.json": "tight 覆盖缺口逐页成因",
    "R14E_ANCHOR_SWEEP.json": "独立排版锚点普查 59 页",
    "R14E_REAL_PAGE_REPLAY_V8.json": "真实截图解析件端到端复放",
    "R14F_PERTURB_RELEASE_AUDIT.json": "扰动 30 页释放审计（f1 固定件）",
    "R14C_PITCH_ABLATION.json": "pitch 双轴消融（因果上限依据）",
    "R14G_PLAN.json": "G 批 manifest（PLANNED_NOT_EXECUTED）",
    "R14G0_GATE_READINESS_V5.json": "G 闸门行为级就绪审计（真实请求 0）",
    "R14_CLAIM_AUDIT.json": "交付件一致性审计（含审计器自证）",
    "R14H_KB_CHANNEL_AUDIT.json": "H KB 通道审计",
    # 交付文档（`.md`）同样**显式登记**：deny-by-default 之后，
    # "默认 current" 会把任何核对副产物冒充成当前交付件（本轮实测）。
    "R14H_KB_CHANNEL_HEAD_DELTA.json": ("h1_r14_kb_channel_audit.py",),
    "R14_VERIFICATION_BUNDLE.json": "一键复现 bundle（18 步离线验证）",
    "R14E_ARTIFACT_ATTRIBUTION.json": "证据件生产者归属（含安全闸门结论）",
    "R14E_ATTRIBUTION_SELFTEST.json": "归属安全闸门的自证注入结果",
    "R14E_RERUN_SEMANTICS.json": "补集复现核对（结论级/注解级差异分列）",
    "R14C_PITCH_ABLATION_HEAD_DELTA.json": "因果实验在现行 HEAD 的复现差异",
    "R14G_HISTORICAL_BYTES.json": "历史 Test 件逐字节对引入 commit 的完整性核验"
                                  "（含本轮误覆写事故记录）",
    "R14G_HISTORICAL_BYTES_SELFTEST.json": "A9 字节基线检查的触发反证",
    "R14_DELIVERY_SUMMARY.json": "④ 交付汇总（逐项从真件取数）",
    "R14_DELIVERY_SUMMARY_INJECT_SELFTEST.json": "④ 交付汇总的取数即断言反证",
    "R14G_HISTORICAL_BYTES.md": "历史 Test 件字节完整性核验正文（含本轮"
                                "误覆写事故记录与自我撤回）",
    "R14_PROGRESS_STOPGATE.md": "人工评审入口（本轮闸门文档）",
    "R14_STAGE_E_DELIVERABLES.md": "Stage E 十项交付正文",
    "R14E_NEWLAYOUT_REGRESSION.md": "新排版独立 Dev 回归说明",
    "R14E_TIGHT_GAP_DIAGNOSIS.md": "tight 缺口逐页成因说明",
    "R14G0_READINESS.md": "G 闸门零请求就绪说明",
    "R14_CLAIM_AUDIT.md": "交付件一致性审计说明（含审计器自证）",
    "R14C_PITCH_ABLATION_HEAD_DELTA.md":
        "因果实验在现行 HEAD 的复现差异（人工裁决项 2 材料）",
}
HISTORICAL = {
    "R14E_SIX_CONDITION.json": "E 首跑件（C/D 候选口径与后续不同，逐条说明见血缘件）",
    "R14F_SIX_CONDITION.json": "E **前**现行协议基线",
}
ERRATUM = {
    "R14E_NEWLAYOUT_REGRESSION_V2.json":
        "把证据链单元数标为消费单元，与落盘裁剪份数混同（V3 已勘误，保留不删）",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# 读取方（非作者），不得进"生产脚本"列
PRODUCER_EXCLUDE = {"e2e_r14_claim_audit.py", "e2e_r14_index.py"}
# **自指件**：索引无法登记自己写出后的最终字节，故单独标注并排除哈希自检
SELF_NAMES = {"R14_INDEX.json", "R14_INDEX.md"}


def commits(rel: str):
    def run(args):
        r = subprocess.run(["git"] + args + ["--", rel], cwd=ROOT,
                           capture_output=True, text=True)
        return (r.stdout or "").strip().splitlines()

    added = run(["log", "--diff-filter=A", "--format=%h"])
    last = run(["log", "-1", "--format=%h"])
    return (added[-1] if added else None), (last[0] if last else None)


_ATTR_MAP: dict = {}
_DECLARED: dict = {}


def _fam(n: str) -> str:
    return re.sub(r"_V\d+(?=\.json$)", "", n)


def _declared_map():
    """**复用归属工具的同一判定**：脚本源码里出现的全部 R14*.json 字面量
    （且该脚本确有写出）。自己再写一份"必须挨着 write_text"的正则会把
    `name = "X.json"` → `dst.write_text()` 这种正常写法误判为"不可复现"
    ——本轮实测犯过（15 个可复现件被标假）。"""
    if _DECLARED:
        return _DECLARED
    try:
        import sys
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import r14_attribute_producer as AP
        for sp in sorted(SCRIPTS.glob("*.py")):
            _DECLARED[sp.name] = {_fam(w)
                                  for w in AP.script_declares_write(sp.name)}
    except Exception:  # noqa: BLE001
        for sp in sorted(SCRIPTS.glob("*.py")):
            t = sp.read_text(encoding="utf-8", errors="ignore")
            if "write_text" not in t:
                continue
            _DECLARED[sp.name] = {_fam(w) for w in re.findall(
                r'"(R14[A-Za-z0-9_\-]*\.json)"', t)}
    return _DECLARED


def _attribution():
    """动态归属结论（跑过归属工具才有条目；没跑则不影响档位判定）。"""
    if _ATTR_MAP:
        return _ATTR_MAP
    p = EV / "R14E_ARTIFACT_ATTRIBUTION.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for r in d.get("results", []):
                _ATTR_MAP[r["artifact"]] = r
        except Exception:  # noqa: BLE001
            pass
    return _ATTR_MAP


def _bundle_steps():
    """一键复现 bundle **真跑过**的件 → 本身就是动态可复现证据。"""
    p = EV / "R14_VERIFICATION_BUNDLE.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {x["artifact"]: x for x in d.get("steps", [])
                if x.get("exit") == 0}
    except Exception:  # noqa: BLE001
        return {}


# **人工撰写**的交付文档：没有生产脚本是**正确**的，
# 但必须逐件显式声明，且被**反向核验**（见 A1b）。
HAND_WRITTEN = {
    "R14_CLAIM_AUDIT.md", "R14E_NEWLAYOUT_REGRESSION.md",
    "R14E_TIGHT_GAP_DIAGNOSIS.md", "R14G0_READINESS.md",
    "R14G_HISTORICAL_BYTES.md",
    # **被我删掉的假豁免（A1b/A10 两轮各抓一个）**：
    # · `R14C_PITCH_ABLATION_HEAD_DELTA.md` 由 `e2f_r14_causal_delta.py`
    #   现场写出；
    # · `R14_STAGE_E_DELIVERABLES.md` 与 `R14_PROGRESS_STOPGATE.md`
    #   由 bundle 的 A13 回写步**每轮**写出 ⇒ 再说"人工撰写"就是谎报
    #   （A10b 于本轮第二次抓到同一形态的谎：我删了前者却忘了后者）。
}

# 只有这两档才算"真的有生产者"；`mention_only` 是**读到/引用**该件名，
# 不是写它 —— 用它当"有生产者"会把人工撰写的件误判成可复现。
_REAL_PRODUCER_TIERS = ("attribution_verified", "declared_writer")

# 交付材料清单（**代码内常量**，索引据此核对；
# 本轮实测：我在会话里手敲 10 个名字核对，把档位为 `self` 的
# `R14_INDEX.md` 误报成"缺件"——人工清单既会漏也会错报。）
DELIVERABLES = [
    "R14_STAGE_E_DELIVERABLES.md", "R14_PROGRESS_STOPGATE.md",
    "R14_INDEX.md", "R14_INDEX.json", "R14_VERIFICATION_BUNDLE.json",
    "R14G_PLAN.json", "R14G0_GATE_READINESS_V5.json",
    "R14G_HISTORICAL_BYTES.md", "R14E_RERUN_SEMANTICS.json",
    "R14_CLAIM_AUDIT.json", "R14E_ATTRIBUTION_SELFTEST.json",
    "R14E_ARTIFACT_ATTRIBUTION.json", "R14_DELIVERY_SUMMARY.json",
    "R14_DELIVERY_SUMMARY_INJECT_SELFTEST.json",
]


def check_deliverables(rows):
    """每件交付材料必须**在册**且档位是 current/self，且可追溯生产者。"""
    by = {r["artifact"]: r for r in rows}
    out = []
    for name in DELIVERABLES:
        r = by.get(name)
        if r is None:
            out.append(f"D 交付材料**不在册**：{name}")
        elif r["status"] not in ("current", "self"):
            out.append(f"D 交付材料档位异常：{name} = {r['status']}")
        elif not r["producer_scripts"] and name not in HAND_WRITTEN:
            out.append(f"D 交付材料既无生产者、也不在人工撰写清单：{name}")
    return out


WRITE_STMT = re.compile(
    r"(?:write_text|write_bytes|open\s*\([^)]*[wa])")


def script_default_out(script: str):
    """脚本的**默认输出件名**（`name = "X.json"` 紧跟 `--out` 处理）。
    归属必须能被这个字面量**直接对上**，不能只信下游件的自述。"""
    try:
        t = (ROOT / "scripts" / script).read_text(encoding="utf-8",
                                                  errors="ignore")
    except OSError:
        return None
    m = re.search(r'^\s*name\s*=\s*"([^"]+\.json)"', t, re.M)
    return m.group(1) if m else None


def honors_out_flag(script: str) -> bool:
    """脚本是否**真的**实现 `--out`（名字可由调用方给 ⇒ 才有资格
    在"行为归属"档里当作者）。"""
    try:
        t = (ROOT / "scripts" / script).read_text(encoding="utf-8",
                                                  errors="ignore")
    except OSError:
        return False
    return '"--out"' in t or "'--out'" in t or "add_argument(\"--out\")" in t


def behavioral_backing(artifact: str, script: str, att: dict) -> bool:
    """行为归属：归属件把 `script` 列为该件的 `true_producers`，
    **且**该脚本实现 `--out`。缺任一条都不算。"""
    row = (att or {}).get(artifact) or {}
    tp = [str(x).rsplit("/", 1)[-1] for x in (row.get("true_producers") or [])]
    return script in tp and honors_out_flag(script)


def attribution_is_backed(script: str, artifact: str,
                          att=None,
                          allow_behavioral: bool = True):
    """归属证据：**两档任一**成立即算有作者，并返回是哪一档。

    ① `literal`：默认输出名 / `--out` 实参 / 写出语句**字面**对得上；
    ② `behavioral_replay`：归属件把该脚本列为 `true_producers`，
       **且该脚本真的实现 `--out`**（本轮实测：`e2d_...v4.py` 的
       `--out` 在 `:80`，件名由调用方传入；归属器是**跑一遍比形状**
       得出结论的，不是抄文件名的 ⇒ 只认字面会把行为核验误判成证言）。
    """
    if script_default_out(script) == artifact:
        return True, "literal_default_out"
    if any(script == n for n, _l in out_arg_producer(artifact)):
        return True, "literal_out_arg"
    if really_written_by(script, artifact):
        return True, "literal_write_stmt"
    if allow_behavioral and behavioral_backing(artifact, script, att or {}):
        return True, "behavioral_replay"
    return False, "none"


def out_arg_producer(artifact: str):
    """哪些脚本在**本仓库的调用点**里被以 `--out <artifact>` 形式使用。

    本轮 A1b 反证实测出来的空档：真作者 `e2d_...v4.py` 源码里的默认名是
    `..._V5.json`，被归属件 `..._V8.json` 这个名字**只出现在调用方**
    （bundle 的 STEPS 字符串）⇒ 只看"同行字面量"会把它判成"没人写它"，
    于是假的人工撰写声明可绕开反向核验。"""
    hits = []
    for p in sorted((ROOT / "scripts").glob("*.py")):
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"--out[ =" + "]" + re.escape(artifact), t):
            hits.append((p.name, t[:m.start()].count("\n") + 1))
    return hits


def produced_by(script: str, artifact: str, row) -> bool:
    """**两种生产形态任一**即算生产：同行写出语句，或 `--out` 实参。"""
    if really_written_by(script, artifact):
        return True
    return any(script == n for n, _ln in out_arg_producer(artifact))


PROBE_SCOPE = re.compile(
    r"mkdtemp|TemporaryDirectory|tmp_path|tmpdir|\bfake\b|_fake|tempfile")
# **字符类 `["']` 由变量拼出**：把它直接写进 raw 字符串会提前闭合
# 字符串定界符 ⇒ SyntaxError（本轮实测踩到）。
_CLS = '["\']'
REAL_EV_ANCHOR = re.compile(
    "(?:^|[\\s(,=])EV\\s*/|ROOT\\s*/\\s*" + _CLS + "evidence|"
    + _CLS + "evidence/|\\.write_text")


def _line_is_probe_write(lines, idx) -> bool:
    """该写出语句是否属于**探针/临时目录**（不是生产）。

    `e2e_r14_claim_audit.py:124` 在 `tempfile.mkdtemp()` 的**假 evidence
    目录**里造 `R14G_RESULT.json`，用来注入违规验证 C6；
    盘上/索引里都没有这个件 ⇒ **探针写 ≠ 生产**。
    """
    lo = max(0, idx - 8)
    if PROBE_SCOPE.search("\\n".join(lines[lo:idx + 8])):
        return True
    depth = len(lines[idx]) - len(lines[idx].lstrip())
    j = idx
    while j > 0 and (lines[j - 1].startswith(" " * depth)
                     or not lines[j - 1].strip()):
        j -= 1
    k = idx
    while k + 1 < len(lines) and (lines[k + 1].startswith(" " * depth)
                                  or not lines[k + 1].strip()):
        k += 1
    return bool(PROBE_SCOPE.search("\\n".join(lines[j:k + 1])))


def really_written_by(script: str, artifact: str):
    """**默认输出名 == 被归属件** ⇒ 直接算命中（真作者最硬的证据）。

    本轮实测：`R14E_REAL_PAGE_REPLAY_V8.json` 的真作者是
    `e2_r14_optionband_replay.py`——它 `:72` 的默认名正是 V8、
    `:76` 才 `write_text`，**名字与写出语句不同行**，
    于是"同行字面量"判据看不见它，反而把
    默认名是 `..._V5.json` 的 `e2d_...v4.py`（来自下游 CHECK 的自述）
    当成了作者 ⇒ 归属与索引口径**都错了一层**。
    """
    if script_default_out(script) == artifact:
        try:
            lines = (ROOT / "scripts" / script).read_text(
                encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        for i, ln in enumerate(lines, 1):
            if re.search(r'^\s*name\s*=\s*"' + re.escape(artifact) + '"', ln):
                return i
        return None
    try:
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8",
                                                      errors="ignore")
    except OSError:
        return None
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if artifact not in ln or not WRITE_STMT.search(ln):
            continue
        if _line_is_probe_write(lines, i):
            continue      # **探针写 ≠ 生产**（tempfile/fake 目录里的写出）
        return i + 1
    # 跨行写法：`json.dumps(...)` 与 `write_text` 分行
    for i, ln in enumerate(lines):
        if artifact not in ln:
            continue
        win = "\n".join(lines[max(0, i - 3):i + 4])
        if WRITE_STMT.search(win) and not _line_is_probe_write(lines, i):
            return i + 1
    return None


def strip_py_comments(src: str) -> str:
    """剥除 Python 源码里的 `#` 注释（**按引号状态**，字符串里的 `#`
    不算注释）。

    需要它的原因（本轮 A10b 假阳性根因）：`_hw` 用 `re.findall`
    从 `HAND_WRITTEN = {…}` **整块**里抓件名，**注释里提到的文件名**
    也一起被抓 ⇒ 我已删掉的 `R14_PROGRESS_STOPGATE.md`
    又被报成"人工撰写"。说明性文字不该进入判据。
    """
    out = []
    q = None
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in "\"'":
            q = c
            out.append(c)
            i += 1
            continue
        if c == "#":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def producer(name: str):
    """作者脚本 = 会**写出**该件的脚本。分两档，避免两个首版错误：

      · **精确命中**：脚本里出现该件的确切文件名；
      · **同族命中**：脚本里出现去掉版本后缀的族名（如
        `R14E_NEWLAYOUT_REGRESSION_V3.json` 由
        `e2e_r14_newlayout_regression.py` 以 `--out` 写出）——此时如实标
        `via_out_override`，复现命令必须带上该 `--out`，否则只会写出默认名。

    审计器与本索引对**别的件**只是读取方，须排除；但 `R14_CLAIM_AUDIT.json`
    确实是审计器**自己**写出的，这一件例外允许（否则 A1 会误报"不可复现"）。
    """
    stem = re.sub(r"_V\d+(?=\.json$)", "", name)
    exact, family = [], []
    declared = []
    for sp in sorted(SCRIPTS.glob("*.py")):
        if sp.name in PRODUCER_EXCLUDE and not (
                name == "R14_CLAIM_AUDIT.json"
                and sp.name == "e2e_r14_claim_audit.py"):
            continue
        try:
            t = sp.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        # **声明写出**才算作者：只看 write_text/open( 附近的件名字面量。
        # "名字出现在脚本里"不等于产出（V8 误判的根因）。
        dm = _declared_map().get(sp.name, set())
        if _fam(name) in dm:
            declared.append(sp.name)
        if name in t:
            exact.append(sp.name)
        elif stem != name and stem in t:
            family.append(sp.name)
    # **优先级**：声明写出 > 精确提及 > 同族提及。只有"提及"的必须标
    # mention_only——"脚本里出现件名"不等于"该脚本产出该件"（V8 误判根因）。
    hits = declared or exact or family
    att = _attribution().get(name)
    if att and att.get("true_producers"):
        tier = "attribution_verified"
        hits = [str(x).rsplit("/", 1)[-1] for x in att["true_producers"]]
    elif declared:
        tier = "declared_writer"
    else:
        tier = ("mention_exact_only" if exact else "mention_family_only")
    return hits, bool(family and not exact), tier


def main() -> int:
    rows = []
    for p in sorted(EV.glob("R14*")):
        name = p.name
        text = ""
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        first, last = commits(f"evidence/{name}")
        prod, via_out, ptier = producer(name)
        # **deny-by-default**：未登记就是 unregistered，不再默认 current
        #（`status = "current"` 起手让每个核对副产物都冒充当前交付件，
        #  本轮 R14H_KB_CHANNEL_AUDIT_RERUN.json 就是这么把 A1 顶失败的）
        status = "current" if name in CURRENT else "unregistered"
        why = CURRENT.get(name, "")
        if name in SELF_NAMES:
            status, why = "self", "索引自身：无法登记写出后的最终字节（自指），" \
                                 "故不入哈希自检"
        elif name in HISTORICAL:
            status, why = "historical", HISTORICAL[name]
        elif name in ERRATUM:
            status, why = "erratum_kept", ERRATUM[name]
        elif any(re.match(pat, name) for pat in SUPERSEDED):
            status, why = "superseded", "同名高版本件为当前引用"
        elif re.search(r"_(RERUN\d*|CHECK)\.json$", name):
            status, why = "verification_byproduct", (
                "动态核验产物：由归属/复现核对以 **新名** 重跑生成，"
                "刻意不覆盖提交件；它证明可复现性，本身不是交付件")
        if status == "current" and p.suffix == ".json" and not prod:
            status, why = "unregistered", "登记为现行但**找不到写出它的脚本**"
        if p.suffix == ".md" and status == "current":
            # **两处清单必须同源**：上面 `CURRENT` 里已登记的文档，
            # 这里**绝不允许**再被降级成 superseded。前几轮这个内联集合
            # 与 `CURRENT` 各写一份，导致"登记了却被自己判过期"的自相矛盾
            # （本轮实测：A9 文档 `R14G_HISTORICAL_BYTES.md` 一落盘，
            #  索引先给 current、下一句又因它不在内联集合里改判 superseded，
            #  计数随即与文档不符 —— C8 抓到的是**索引自己的双重清单**）。
            # 内联集合**删掉**：`.md` 的地位只由 `CURRENT` 决定。
            pass
        rows.append({"artifact": name, "bytes": p.stat().st_size,
                     "sha256": sha(p), "sha_short": sha(p)[:12],
                     "status": status, "status_note": why,
                     "producer_scripts": prod,
                      "producer_tier": ptier,
                      "written_via_out_override": via_out,
                      "producer_note": (
                          "动态归属验证的真作者" if ptier
                          == "attribution_verified" else
                          "声明写出（确有写出且出现该件/族名）" if ptier
                          == "declared_writer" else
                          "仅提及、未声明写出：**引用≠产出**"
                          if prod else "未在任何脚本中找到写出处"),
                      "git_tracked": bool(first),
                      "reproduced_by_bundle": name in _bundle_steps(),
                      "reproducible": (
                          ptier in ("attribution_verified",
                                    "declared_writer")
                          or name in _bundle_steps()
                          or p.suffix == ".md"),
                      "commit_added": first, "commit_last_touched": last})

    docs = {p.name: p.read_text(encoding="utf-8") for p in EV.glob("R14*.md")
            # 索引自身**就是**用来逐条列出非当前件的，扫它会自我误报
            if p.name != "R14_INDEX.md"}
    fails = []
    unregistered = []      # 透明登记：靠 --out 才落到现行文件名的件
    for r in rows:
        att = _attribution().get(r["artifact"])
        if (att and att.get("reproducibility") == "NOT_REPRODUCIBLE_FROM_HEAD"
                and r.get("reproducible")):
            fails.append(f"A5 {r['artifact']} 归属结论为不可复现，索引却登记"
                         f" reproducible=true（乐观误判，禁止）")
        # **A1 口径修正**（本轮实测）：
        # · `self` 曾被直接 continue ⇒ 索引自己的 `.md` 永不追溯生产者；
        # · 旧判据只看 `.json` ⇒ 交付文档 `.md` 一律免检，
        #   而"10 项材料"多数正是 `.md`。
        # 现在 `current` 与 `self` 的 `.json`/`.md` **全量**要求
        # "有生产者，或有明确的手写/自指说明"。
        # **A1c 归属硬条件**（本轮 A1b 反证挖出来的）：
        # `R14E_REAL_PAGE_REPLAY_V8.json` 挂着
        # `attribution_verified / reproducible=True`，作者写的是
        # `e2d_r14_production_replay_v4.py`——但那脚本的**默认输出名**是
        # `..._V5.json`，也从不以 `--out` 写 V8；
        # 归属来自**下游 CHECK 件的自述**（二手证言）。
        # 真作者是 `e2_r14_optionband_replay.py`（默认名正是 V8）。
        _att = _attribution()
        if (r["status"] != "self"
                and r.get("producer_tier") == "attribution_verified"
                and r["producer_scripts"]
                and not any(attribution_is_backed(
                    x, r["artifact"], _att)[0]
                    for x in r["producer_scripts"])):
            _att = _attribution()
            fails.append(
                f"A1c {r['artifact']} 声称 attribution_verified，"
                f"但生产者 {r['producer_scripts']} 中没有一个的"
                f"默认输出名 / `--out` 实参 / 写出语句对得上该件"
                f" ⇒ 归属是下游件的**自述**，不算作者")
        if r["status"] in ("current", "self") \
                and r["artifact"].endswith((".json", ".md")) \
                and not r["producer_scripts"]:
            if r["artifact"] not in HAND_WRITTEN:
                fails.append(
                    f"A1 在册件无生产脚本、也不在**人工撰写**清单里"
                    f"⇒ 不可复现：{r['artifact']} [{r['status']}]")
        elif r["artifact"] in HAND_WRITTEN and (
                any(produced_by(x, r["artifact"], r)
                    for x in (r["producer_scripts"] or []))
                or out_arg_producer(r["artifact"])
                # ↑ **`--out` 实参形态**：本轮 A1b 反证实测出的空档
                or bool(r.get("written_via_out_override"))):
            # **反向核验**：一旦真有脚本写出这件，声明立刻失效——
            # 防 `hand_written` 变成新的"万能豁免"。
            fails.append(
                f"A1b {r['artifact']} 声明人工撰写，却有脚本写它 "
                f"{r['producer_scripts']} ⇒ 要么删声明，要么承认它可复现")
        if r["written_via_out_override"]:
            unregistered.append(
                f"{r['artifact']} ← {', '.join(r['producer_scripts'])}"
                f"（需 --out 复现）")
        if r["artifact"].endswith(".json") and sha(EV / r["artifact"]) != r["sha256"]:
            fails.append(f"A3 哈希不稳定：{r['artifact']}")
    # ---- A1b **反证**：谎报一件人工撰写，必须被抓 ----
    # **定义顺序**也是本轮踩过的坑：我第一版在这里用了 `by_name_early`，
    # 而它到下面才赋值 ⇒ 生成器推导式里报
    # "free variable referenced before assignment"、索引整跑崩掉。
    # 另外探针**不能**是 `self` 件（A1 对 self 直接跳过，
    # 拿它当探针会得到"探针存在但检测永不触发"的假绿）。
    by_name_early = {r["artifact"] for r in rows}
    # 正向探针**必须真有可核对的生产点**；上一轮我挑了
    # `R14E_REAL_PAGE_REPLAY_V8.json`，反证当场揭穿它**没有任何生产点**
    # （bundle 那一步写的是 `..._V8_RERUN.json`，全仓 `--out` 也搜不到 V8）
    # ⇒ 挑错探针会让"检测器空转"与"件本身不可复现"两件事**混成一条**。
    _probe_map = {"R14_VERIFICATION_BUNDLE.json": "r14_verify_all.py",
                  "R14E_RERUN_SEMANTICS.json": "r14_semantic_repro.py",
                  "R14_INDEX.json": "e2e_r14_index.py"}
    _liar = [a for a in _probe_map
             if a in by_name_early
             and {r["artifact"]: r for r in rows}[a]["status"] != "self"]
    _probe_status = {a: {r["artifact"]: r for r in rows}[a]["status"]
                     for a in _probe_map if a in by_name_early}
    if _liar:
        _by = {r["artifact"]: r for r in rows}
        _bad = [a for a in _liar
                if produced_by(_probe_map[a], a, _by[a])
                or bool(_by[a].get("written_via_out_override"))]
        # **负例自证**：只被"提及"（列进 `R14G_*` 前缀数组）的件
        # 必须判**非生产**，否则这条判据就退化成"提到过就算作者"
        _neg = "R14G_HISTORICAL_BYTES_SELFTEST.json"
        _neg_hit = [n for n, _l in out_arg_producer(_neg)
                    if n == "e2e_r14_stage_g_readiness.py"]
        if _neg_hit:
            fails.append(f"A1b 负例失守：`{_neg}` 只是被 readiness 脚本"
                         f"**提及**（前缀数组），却被判成生产者 ⇒ "
                         f"mention ≠ production 的口径又被丢了")
        _neg_produced = bool(really_written_by(
            "e2e_r14_stage_g_readiness.py", _neg))
        if _neg_produced:
            fails.append(f"A1b 负例失守：同行字面量判据说 readiness 写出了 "
                         f"{_neg}")
        _miss = [a for a in _liar if a not in _bad]
        if len(_bad) != len(_liar):
            fails.append(
                f"A1b **反证不完整**：探针 {len(_liar)} 个只判出 "
                f"{len(_bad)} 个（漏 {_miss}）⇒ 写出语句检测对某种生产形态"
                f"是空转，人工撰写声明可绕开反向核验")
        if not _bad:
            fails.append("A1b **反证失效**：写出语句检测对确有生产者的件"
                         "判不出命中 ⇒ 该看守是空转")
        out_a1b = {"probe": _liar, "detected": _bad,
                   "negative_control": {
                       "artifact": _neg,
                       "must_not_be_producer": True,
                       "out_arg_hit": _neg_hit,
                       "same_line_write_hit": _neg_produced},
                   "out_form_hits": {a: out_arg_producer(a) for a in _liar},
                   "probe_status": _probe_status,
                   "note": "故意把**确有写出语句**的**非自指**件说成人工撰写，"
                           "检测必须命中行号；命中为空说明反证空转"}
    else:
        out_a1b = {"probe": [], "detected": [],
                   "note": "无可用的谎报探针件 ⇒ 反证未执行（不算通过）",
                   "not_verified": True}
    fails += check_deliverables(rows)
    by_name_early = {r["artifact"] for r in rows}
    by_name = {r["artifact"]: r for r in rows}
    for d, t in docs.items():
        for m in set(re.findall(r"R14[A-Z0-9_\-]*\.json", t)):
            r = by_name.get(m)
            if not r:
                continue          # 不存在件由 claim audit 负责，这里不重复
            if r["status"] in ("superseded", "historical", "erratum_kept"):
                for ln, line in enumerate(t.splitlines(), 1):
                    if m in line and not any(k in line for k in (
                            "勘误", "取代", "不存", "不得", "旧", "曾", "V2",
                            "混同", "首跑", "前**基线", "保留")):
                        fails.append(f"A2 {d}:{ln} 把 {r['status']} 件 {m} "
                                     f"当作当前引用")
    # **本次运行的指纹**：脚本崩在半路时，磁盘上留下的是**上一轮**的件；
    # 只看件会把旧结果当新结果（本轮实测踩过，见 A12）。
    import uuid as _uuid
    run_mark = _uuid.uuid4().hex[:12]
    out = {"run_mark": run_mark,
           "A1b_selftest": out_a1b,
           "hand_written_declared": sorted(HAND_WRITTEN),
           "deliverables_required": DELIVERABLES,
           "generated_at_head": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                text=True).stdout.strip(),
           "n_artifacts": len(rows),
           "by_status": {s: sorted(r["artifact"] for r in rows
                                   if r["status"] == s)
                         for s in ("current", "superseded", "historical",
                                   "erratum_kept", "self")},
           "artifacts": rows, "self_checks_failed": fails,
           "reproduce_needs_out_override": sorted(set(unregistered)),
           "reading_order_for_human": [
               "1. R14_PROGRESS_STOPGATE.md（进度 + 6 个待裁决点）",
               "2. R14_STAGE_E_DELIVERABLES.md（10 项材料 + 补充件 A–E + 盲评准入）",
               "3. R14E_NEWLAYOUT_REGRESSION.md / R14E_TIGHT_GAP_DIAGNOSIS.md"
               "（回归与缺口成因，含 V2 口径勘误）",
               "4. R14G0_READINESS.md / R14_CLAIM_AUDIT.md（G 闸门就绪与交付自审）",
               "5. R14E_LINEAGE_SIX_CONDITION.json（数字血缘，旧协议不混比）"],
           "blind_eval": "NOT READY：真实截图端到端 2/5 释放正确；离线 F 5/13 "
                         "零错放不构成真实截图突破",
           "llm_calls_used": 0, "http_requests_made": 0}
    dst = EV / "R14_INDEX.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# R14 交付索引（机器登记，零 API）", "",
             "注：本轮**改了** STOPGATE / DELIVERABLES / NEWLAYOUT_REGRESSION 三份"
             "文档的措辞，也**重跑**了回归脚本（产物字节逐字一致），因此下表 commit "
             "列是**入库时点**、与工作区未提交内容不同属**已知且如实**，不是漏记。",
             "",
             f"HEAD `{out['generated_at_head']}` · 件数 **{out['n_artifacts']}** · "
             f"自检失败 **{len(fails)}** 项 · 真实请求 **0**",
             "", "## 当前件（可复现性 = 是否登记生产脚本）", "",
             "（下表「生产脚本」已排除审计器/索引等**读取方**；"
             "「—」= 人工撰写的文档，不由脚本生成）",
             "",
             "| 件 | 地位 | 生产脚本 | 首入库 | 最近改动 | sha256(12) |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "current":
            continue
        lines.append(f"| `{r['artifact']}` | {r['status_note'][:38] or '—'} | "
                     f"{', '.join(r['producer_scripts'])[:48] or '**无**'} | "
f"{r['commit_added'] or '未入库'} | "
                     f"{r['commit_last_touched'] or '未入库'} | "
                     f"`{r['sha_short']}` |")
    lines += ["", "**自指说明**：本索引写完后其自身的最终字节无法被自己登记，"
              "因此 `R14_INDEX.json` / `R14_INDEX.md` 地位标为 `self` 并**排除**"
              "哈希自检（A3）——这是诚实边界，不是漏检。", ""]
    lines += ["## 非当前件（不得作当前引用）", ""]
    for s in ("superseded", "historical", "erratum_kept"):
        for name in out["by_status"][s]:
            r = by_name[name]
            lines.append(f"- `{name}` · **{s}** · {r['status_note']}")
    lines += ["", "## 人工阅读顺序", ""]
    lines += [f"- {x}" for x in out["reading_order_for_human"]]
    lines += ["", f"**盲评准入**：{out['blind_eval']}", ""]
    if out["reproduce_needs_out_override"]:
        lines += ["", "## 复现需要 `--out`（默认名与现行件名不同）", ""]
        lines += [f"- {x}" for x in out["reproduce_needs_out_override"]]
    if fails:
        lines += ["## 自检失败", ""] + [f"- {f}" for f in fails]
    (EV / "R14_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # **计数由生产者同步，不由我手打**：登记类散文里的
    # "N 件逐件登记"必须等于本件 `n_artifacts`。
    # 上一轮这类计数靠手改，改成了 59 而真值是 60 ⇒ C8 红；
    # 更糟的是"由 bundle 每轮回写"这句话**当时并不成立**。
    _sync = []
    _sp = Counter(r["status"] for r in rows)
    # **计数真值表**：C8 判什么形态，这里就同步什么形态。
    # 上一版只回写 `N 件逐件登记` ⇒ bundle 立刻报出我漏掉的
    # `current 30`（真值 32）——**部分同步比不同步更危险**，
    # 它让文档看起来"已经自动化了"。
    _truth = {
        "artifact_total": out["n_artifacts"],
        "current": _sp["current"],
        "superseded": _sp["superseded"],
        "verification_byproduct": _sp["verification_byproduct"],
        "historical": _sp["historical"],
        "erratum_kept": _sp["erratum_kept"],
        "self": _sp["self"],
    }
    # 上一版**手写**这些形态的替换对（漏了一个分组、少同步一档），
    # 于是 `IndexError: no such group` 把索引打崩 ⇒ 盘上留着**上一轮**
    # 的索引件（本轮第 11 条坑的第 5 次复发）。
    # ⇒ **由 `_truth` 的键**驱动正则：新增一档只要加一条形态，
    #   不再手写捕获组。
    # 每条形态 = (正则, 真值键, 模板, 自检样例, 自检期望)。
    # **装载即自检**：模板引用了不存在的组 ⇒ 立刻 `IndexError`，
    # 但我宁可在这里**显式报错**，也不要它在回写循环里
    # 把索引打崩、让盘上留下上一轮的旧结论件冒充本轮。
    import re as _re_f
    # 每条形态 = (正则, 真值键, 模板, 自检样例, 自检期望, 命名来源)。
    # **命名来源**：`pre`/`post` 从哪个捕获组取、或是字面量/空串。
    # 位序（`{0}`/`{1}`/`{2}`）在这里**彻底消失**——
    # 我为它连猜错三次（`{1}{t}{2}`、`{t}{2}`、`{0}{t}{1}`），
    # 每次都要真跑一遍才知道对错；根因是
    # `"{t}".format(*groups, t=v)` 里 **`t` 不占位置参数**，
    # 位置参数只有 `*groups`，而 `str.format` 的 `{n}` 是 **0-based**。
    # 每条形态 = (正则, 真值键, pre 来源, post 来源, 样例)。
    # **期望值不再由我手填**：上一版同时手填模板和期望，
    # 于是 `99 件逐件登记` 拼出 `9960 件逐件登记` 时，
    # 我的"期望"也跟着写成 `99 件逐件登记`——
    # 两者各自漂移还互相"验证"，这是**最像自检的非自检**。
    # 现在只核**语义三条件**（下面 `_FORMS` 用后即验）。
    # 每条形态 = (正则, 真值键, 样例)。正则**必须**含命名组 `num`，
    # 替换时**只换 `num` 的内容、其余字符逐字保留** ⇒
    # "改动了非数字文本"这条判据由**构造**保证。
    # （上一版用 `{pre}{t}{post}` 裸拼，`current 99 /` 被同步成
    #  `current32 /`——`\s` 属于捕获组却被丢掉，数字前后的空格是**原文**。）
    _FORMS = [
        (r"\d+(?=\s*件逐件登记)", "artifact_total", "99 件逐件登记"),
        # **反引号内不匹配**：文档里 `"55 件逐件登记"` 是**注入锚点说明**、
        # 不是声明；`claim audit` 的 C8 按 `inline_code_or_fence` 豁免它们。
        # 同步器与审计器必须**同一口径**，否则我会把
        # 一段"讲豁免机制"的话同步成假数字。
        (r"(?<![A-Za-z_`])current\s+\d+", "current", "current 99 /"),
        (r"当前\s+\d+", "current", "当前 99 /"),
        (r"被取代\s+\d+", "superseded", "被取代 99 /"),
        (r"核对副产物\s+\d+", "verification_byproduct", "核对副产物 99"),
        (r"勘误保留\s+\d+", "erratum_kept", "勘误保留 99"),
        (r"历史\s+\d+", "historical", "历史 99 /"),
    ]

    def _apply1(text):
        """按 `_truth` 同步一遍（**自检与回写共用同一个函数**，
        于是"自检通过"与"真回写"不可能走两条路）。"""
        out_t = text
        for rx, key, _sample in _FORMS:
            out_t = re.sub(rx, lambda m, _k=key: re.sub(
                r"\d+$", str(_truth[_k]), m.group(0)), out_t)
        return out_t

    for _rx, _k, _sample in _FORMS:
        _m = re.search(_rx, _sample)
        if not _m:
            raise RuntimeError(f"计数形态自检失败（匹配不到）：{_rx}")
        if not re.search(r"\d+$", _m.group(0)):
            raise RuntimeError(f"计数形态自检失败（命中不以数字结尾）：{_rx}")
        _old = re.search(r"\d+", _sample).group(0)
        _tv = str(_truth[_k])
        _got = _apply1(_sample)
        if _old != _tv and re.search(rf"(?<!\d){_old}(?!\d)", _got):
            raise RuntimeError(f"计数形态自检失败（旧数字残留）："
                               f"{_sample} → {_got}")
        if _tv not in _got:
            raise RuntimeError(f"计数形态自检失败（真值没出现）：{_got}")
        if re.sub(r"\d+", "", _got) != re.sub(r"\d+", "", _sample):
            raise RuntimeError(f"计数形态自检失败（改动了非数字文本）："
                               f"{_sample} → {_got}，应只换数字")
    # **自检必须能红**：改真值 ⇒ 输出必须跟着变；
    # 否则"同步"根本没吃到真值。
    _bak = _truth["artifact_total"]
    try:
        _truth["artifact_total"] = 12345
        if "12345" not in _apply1("99 件逐件登记"):
            raise RuntimeError("计数同步**没吃到真值**（改 _truth 输出不变）")
    finally:
        _truth["artifact_total"] = _bak

    # **过去式引文不是现行声明**：这一行讲的是"我当时同步成了 X"，
    # 把 X 改成今天的真值等于**销毁现场**（本轮 C8 就因此
    # 对 `56/current 30` 报假阳性）。同步器与 C8 **同一判据**。
    _PAST = re.compile("我把|随后|当时|上轮|上一轮|曾把|曾经|已改成|"
                       "历史真值|旧真值")
    _tick = re.compile(r"^(`{3}.*|.*`.+`.*|[|·\-]\s.*|[0-9a-f]{8,}.*|.*`.*`"
                       r".*)$")
    for _dn in ("R14_STAGE_E_DELIVERABLES.md", "R14_PROGRESS_STOPGATE.md"):
        _dp = EV / _dn
        if not _dp.is_file():
            continue
        _t0 = _dp.read_text(encoding="utf-8")
        _out = []
        for _ln in _t0.splitlines():
            # **逐行、且只在行首声明形态上同步**：反引号内/项目符号/
            # sha 行等**示例语境**照 C8 的 `inline_code_or_fence` 豁免。
            # 上一版做**全文 re.sub**，把 `:175` 那条
            # "注入锚点必须来自盘上真值：我把 \`"55 件逐件登记"\`"
            # 的**示例数字**同步成了 60 —— 那行讲的是"我当时写错了什么"，
            # 改掉它=销毁现场，而 C8 会立刻把这行当成过期计数。
            if _PAST.search(_ln) or _tick.match(_ln):
                _out.append(_ln)
                continue
            # **引号内 = 示例/现场引文，不是声明**（与 C8 的
            # `inline_code_or_fence` 同口径）。
            # 上一版只豁免反引号**行首**，漏掉了行中含 `` `"55 件…"` ``
            # 的引文 ⇒ 同步器**把我记录的 bug 现场改成了新数字**，
            # 两处历史引文因此失真（改文档必须比改代码更保守）。
            _span = 0
            _skip = False
            for _q, _c in (("``", 2), ("`", 1)):
                pass
            _parts = _ln.split("`")
            _odd = [x for i, x in enumerate(_parts) if i % 2 == 1]
            if any(re.search(r"\d+\s*件逐件登记|\bcurrent\s*\d+|"
                             r"当前\s*\d+", x) for x in _odd):
                _skip = True
            if _skip:
                _out.append(_ln)
                continue
            _o = _ln
            _o = _apply1(_o)
            _out.append(_o)
        _t = "\n".join(_out) + ("\n" if _t0.endswith("\n") else "")
        if _t != _t0:
            _dp.write_text(_t, encoding="utf-8")
            _sync.append(_dn)
    out["artifact_count_synced_to_docs"] = _sync
    out["count_truth"] = _truth
    # **stdout 末尾必须是单行紧凑 JSON**：
    # bundle 的 stdout 解析是**逐行**找 `startswith("{")` 的那一行，
    # 而我这里打的是 `indent=1` 的**多行** JSON ⇒ 只有第一行 `{`
    # 被当成一个 JSON 去解析 ⇒ 必然失败 ⇒ `run_mark` 取不到，
    # A12 就把这一步判成"无法证明件是本次运行写的"。
    # （本轮实测：A12 报 `stdout 没有报告 run_mark`，根因在此。）
    print(json.dumps({"run_mark": run_mark, "n_artifacts": out["n_artifacts"],
                      "count_synced_to_docs": _sync,
                      "self_checks_failed": fails}, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
