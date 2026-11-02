# ruff: noqa: E501
"""Versioned agent guidance content pack (plan 2026-10-30 §4/§13/§14).

The pack is pure data: no I/O, no service state, no science facts of its own.
Every entry carries an id, a version, the workflows it covers and a coverage
label; the per-workflow prompts are the reviewed drafts from the unified plan
(``ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md`` §13-§14) published as a
versioned, installable content package.  Parameters, defaults and enums are
*not* duplicated here — they are projected from the submit contracts by
``acp.api.workflow_contracts``; a guide never becomes a second parameter
authority.

Coverage labels are honest by construction:

* ``first_batch`` — one of the four composite first-batch scenarios
  (Confsearch / BatchOptimize / PESsearch / scan);
* ``documented_partial`` — the workflow is described and constrained, but its
  MCP consumption entry may not be closed; the guide says so explicitly.

The pack is exposed read-only through ``GET /api/v1/guidance`` (HTTP) and the
``acp_get_guidance`` MCP tool; both return the same versioned payloads with a
stable sha256 digest for caching (plan §4.2: version change, contract mismatch
or a new section requirement forces a re-read).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp.catalog import WORKFLOW_CATALOG

logger = logging.getLogger(__name__)

__all__ = [
    "GUIDE_FORMAT",
    "MAX_GUIDE_BYTES",
    "MAX_INDEX_CARD_BYTES",
    "PACK_VERSION",
    "GuideEntry",
    "audit_pack",
    "get_guide",
    "guide_ids",
    "guidance_index",
    "read_guide",
    "workflow_guide_map",
]

#: Wire format of one guide payload (frozen for caching compatibility).
GUIDE_FORMAT = "acp.guidance.v1"

#: Pack-level version.  Bump when any entry changes; entry digests make cache
#: invalidation per-guide precise.  1.1.0: BatchOptimize role wire-syntax +
#: PESsearch unit/bond-side vocabulary (DA validation round,
#: reports/acp2_mcp_guidance_20261030).  1.2.0: method-selection ladder and
#: artifact-locating topics (agent feedback round 2).  1.3.0: task-level
#: instructions (goal->chain routing, not per-workflow manuals).  1.4.0:
#: task.find_transition_state states the pure-MCP publish dead end and the
#: first-class inline role:"ts" path; artifact_locating names both sp.out
#: layouts (round-7 external-agent feedback).
PACK_VERSION = "1.5.0"

#: Byte budgets (plan §4.2): UTF-8 bytes, not tokens.  A guide that would
#: exceed a caller-supplied bound is returned with explicit omissions — never
#: silently truncated.
MAX_GUIDE_BYTES = 8192
MAX_INDEX_CARD_BYTES = 1024

#: First-batch composite scenarios (plan §1.2/§9).
FIRST_BATCH_WORKFLOWS: tuple[str, ...] = ("Confsearch", "BatchOptimize", "PESsearch", "scan")


@dataclass(frozen=True)
class GuideEntry:
    """One versioned guidance document.

    Attributes:
        guide_id: Stable id, e.g. ``workflow.PESsearch`` or ``topic.recovery``.
        kind: ``shared`` | ``workflow`` | ``orchestration`` | ``topic``.
        title: Human-readable short title.
        version: Entry version (semver-ish string).
        workflows: Workflow ids this entry covers (workflow kind only).
        coverage: ``first_batch`` or ``documented_partial``.
        summary: One-line purpose for discovery cards.
        sections: Ordered mapping section name -> body text.  The body is
            guidance, not a parameter schema and never an execution authority.
        topics: Referenced topic ids (one level of indirection only, plan §4.2).
    """

    guide_id: str
    kind: str
    title: str
    version: str
    workflows: tuple[str, ...] = ()
    coverage: str = "documented_partial"
    summary: str = ""
    sections: Mapping[str, str] = field(default_factory=dict)
    topics: tuple[str, ...] = ()


def _entry_digest(entry: GuideEntry) -> str:
    """Stable sha256 over the semantic content (id/version/sections/workflows)."""
    material = {
        "guide_id": entry.guide_id,
        "version": entry.version,
        "workflows": list(entry.workflows),
        "sections": dict(entry.sections),
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_size_bytes(entry: GuideEntry) -> int:
    return sum(len(text.encode("utf-8")) for text in entry.sections.values())


# ---------------------------------------------------------------------------
# Shared execution prompt (plan §13.2)
# ---------------------------------------------------------------------------

_SHARED_EXECUTION = GuideEntry(
    guide_id="shared.execution",
    kind="shared",
    title="通用执行 Prompt（各工作流共用）",
    version="1.1.0",
    summary="所有工作流共用的执行约束：契约优先、复用工件、授权内直行、证据分级、初猜来源。",
    coverage="first_batch",
    sections={
        "body": (
            "你通过 ACP MCP 协助用户完成受控的计算化学任务。使用当前会话实际开放的工具和服务端契约，"
            "不以本指南代替执行权限、科学验证或参数校验。\n"
            "1. 发现协议（按此四步，不要靠读平台源码猜接口）：⓪acp_capabilities() 的 task_instructions "
            "索引按用户目标给出任务配方（如找过渡态、构象+能量、多参考分析）——命中则先读该 task instruction "
            "作为链路地图；①workflow_contracts 索引给出每个工作流的 coverage、{tool, arguments} 详情指针与"
            "未契约化时的 fallback；②acp_capabilities(workflow=<id>) 读完整 input/method/resources schema、"
            "示例与 constraints；③acp_get_guidance(guide_id) 读工作流/专题指南。区分 registered、当前可执行、"
            "参数覆盖完整与真实验收范围；coverage=not_provided 时按 fallback 行动（用 CLI 或报告缺口），"
            "不读源码不猜字段。\n"
            "2. 优先复用已存在的结构、任务和工件。核对项目、来源 attempt、generation/digest 与电子态；"
            "保留原子顺序和映射。不要猜绝对路径、手填摘要或把展示序号当原子/帧身份。\n"
            "3. 从已有记录或授权读取科学选择；真正缺少且影响计算含义的信息集中询问。参数默认值必须来自契约；"
            "方法升级、改变电荷/电子态、扩大研究范围不能悄悄进行。\n"
            "4. 全自动模式下，授权范围内的选点、发布、提交、等待、读结果与有限修订直接推进。权限不足、来源不明、"
            "超范围或预算不足时停止相关写入并说明原因；不自行批准自己的计划。\n"
            "5. 提交前使用实际开放的预检入口，读取错误与生效配置。对明确可修正的输入错误按字段/rule 修正；"
            "不靠轮换字段、关校验或改变科学目标获取通过。\n"
            "6. 使用平台规定的幂等机制。相同请求重试复用原提交意图和键；回包丢失先找回已有任务。"
            "输入或计划变化时重新预检并绑定新身份。\n"
            "7. 对长任务使用有界等待或建议的查询间隔。区分 MCP 调用成功与任务 completed，"
            "区分整个任务和逐 item/stage 的结果。\n"
            "8. 从 status/结果发现获得产物描述，再显式 prepare/read。检查引用、编码、完整性、截断与摘要作用域；"
            "看不到的部分不推断为通过。\n"
            "9. 读取优化/SCF/方法/结构等实际证据。区分 requested、effective 和 executed；模型解释不是执行事实。"
            "缺失验证保留 unknown/inconclusive，证伪保留 refuted。\n"
            "10. TS/INT 初猜优先从扫描/路径证据生成：默认 PESsearch 键长扫描（中小体系；同一键长轴多点、"
            "能量曲线连续）；大体系、多坐标联动、或 PES 曲线分支切换/点稀疏导致 scan 效果不佳时用 scan"
            "（relaxed/pointwise）逐点优化；已有冻结路径 payload 时用 XtbPathSearch 路径帧。"
            "不推荐手工摆放结构作为初猜：手摆没有扫描能量/路径证据，平台不禁止，但指南不推荐；"
            "若用户坚持手摆，必须说明其无证据性质，并靠频率（恰一个虚频/模式方向）与 IRC/端点验证补足，"
            "不能当成已确认候选。PES/scan 的 TS/INT 选点也只产生初猜，必须通过合法发布进入 BatchOptimize，"
            "再读取逐候选结果；保存成功不是 TS 找到，Batch completed 也不是反应路径验证完成。\n"
            "11. 资源选择：提交前读 acp_capabilities 的 capacity（准入视角的当前可用配额，"
            "非 OS 空闲），nproc/mem 取 available 之下且不超过 admission.limits 的值；不要凭空猜核心数。\n"
            "12. 只对有依据、在范围内且预算允许的失败项或缺失信息追加计算；限制总候选数与补算轮次。\n"
            "13. 会话恢复先查询平台记录，核对已有任务、选点和提交意图，再决定下一步。\n\n"
            "对用户的结果回答包括：实际完成的工作、关键结果及引用、可支持的结论、缺失/失败项、下一步或停止原因。"
        ),
    },
    topics=(
        "topic.inputs_electronic_state",
        "topic.method_energy_comparability",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
)


def _workflow_entry(
    guide_id: str,
    title: str,
    summary: str,
    purpose: str,
    steps: str,
    *,
    coverage: str,
    topics: tuple[str, ...] = (),
    version: str = "1.0.0",
) -> GuideEntry:
    return GuideEntry(
        guide_id=guide_id,
        kind="workflow",
        title=title,
        version=version,
        workflows=(guide_id.split(".", 1)[1],),
        coverage=coverage,
        summary=summary,
        sections={"purpose": purpose, "steps": steps},
        topics=topics,
    )


# ---------------------------------------------------------------------------
# Workflow guides (plan §13.3-§13.17)
# ---------------------------------------------------------------------------

_CONFSEARCH = _workflow_entry(
    "workflow.Confsearch",
    "Confsearch — 构象搜索与筛选",
    "按协议生成构象并筛选/精修，产出有来源、有质量说明的构象集合。",
    (
        "围绕给定分子生成构象并按所选协议筛选/精修。现有协议 xtb-crest、xtb-md、censo-crest、xtbmd-censo；"
        "阶段依赖由协议决定。screen 不选取后续精修候选；纯 xTB 协议不能宣称执行了 DFT 精修。"
        "构象搜索完成不证明全局最低点或构象空间穷尽。"
    ),
    (
        "当前工作流：Confsearch。目标是获得有来源、有质量说明的构象集合。\n"
        "1. 核对分子结构、立体化学、电荷/多重度。已有可信输入时复用；SMILES 映射以服务端物化结果为准。\n"
        "2. 读取当前协议列表与每个协议实际阶段/可用性；不把 registry 的软件并集强加给纯 xTB 场景。\n"
        "3. 绑定筛选与精修策略。screen 允许零精修候选；纯 xTB 路径只报告其实际计算级别；"
        "不提交该工作流不支持的 max_conformers 等参数。\n"
        "4. 预检后提交和等待，分别读取搜索产生、筛选保留、精修选中及实际精修成功数。\n"
        "5. 读取候选结构、能量类别、实际方法和质量记录；精修/未精修混合集合不直接当同级别排序。\n"
        "6. 选择下游候选时说明范围与依据，使用稳定候选引用，保留结构多样性与预算限制。\n"
        "7. 用户要求进一步优化/验证时按候选引用构造 BatchOptimize；只要构象集合时报告筛选范围与局限后结束。\n"
        "8. 阶段失败时区分已有可用集合和未完成部分；只在授权内恢复或补算。\n\n"
        "输出：协议与实际阶段、分阶段计数、候选及来源、方法可比性、覆盖局限、Batch 引用或结束原因。"
    ),
    coverage="first_batch",
)

_BATCHOPTIMIZE = _workflow_entry(
    "workflow.BatchOptimize",
    "BatchOptimize — 批量优化与后续计算",
    "对带角色的结构集合逐项执行配置的阶段（opt/freq/sp/thermo），保留逐 item 来源与执行事实。",
    (
        "对一组带角色（TS/INT）的结构逐项执行配置的阶段。当前 profile 为 opt_only、opt_freq、opt_freq_sp、"
        "opt_freq_sp_thermo。角色生效参数由配置解析器决定，不由名字或 TAG 自动证明科学属性。"
        "PES/scan 初猜必须在本工作流完成所需优化和适用验证；只有 opt_only 不等于完成驻点验证。"
    ),
    (
        "当前工作流：BatchOptimize。将候选初猜推进为可检查的计算结果。\n"
        "1. 读取输入集合与发布状态。PES/scan 初猜的推荐链路：上游 PES review 确认选点后，以 "
        "input.from_artifact（上游 RESULT/result_manifest.json）+ selected_ids（如 pes_ts_frame_005）消费已确认"
        "候选；未确认的推荐点不能直接当最终输入。\n"
        "2. 每个 item 的用途角色必须是 wire 级显式，TS 的两种正确表达：①inline item 携带 role/tag 字段"
        '（role:"ts"）；②from_artifact 候选经 review 确认的角色。关键：未标角色的 inline item 默认按 '
        "INT（最低点优化）执行，method.batch_roles 的 ts 配置不会自动套用到未标角色的 item；预检的 "
        "submission_analysis.effective.items 会列出每个 inline item 解析出的 role/role_source"
        "（未标注 → INT），提交前仍须确认角色标注意图。TS 表示尝试过渡态优化的初猜，INT 表示相应最低点"
        "优化用途。\n"
        "3. 读取 profile 和生效配置；需要频率/单点/热化学时选择包含这些阶段的配置，不把 opt_only 报成 opt_freq。\n"
        "3.5 只交 TS、不交 INT 时：**不要写任何顶层 mirror**（optimization_method/optimization_basis/"
        "single_point_* / 温度压力缩放都只是 int role 的 Workbench 镜像），把方法写在 batch_roles.ts 里；"
        "顶层 mirror 缺 int role/字段会被 422（rule=missing_role_mirror），不会被静默忽略。\n"
        "3.6 input.items 必须与 input.source_type=\"batch_structures\" 同时出现（提交与预检都会 422，"
        "rule=missing_source_type）；用 from_artifact/items_file 时不要给 items。\n"
        "3.7 method.levels 是透传区：level values 只做类型/枚举检查，语义归 CLI/consumer。"
        "opt_recalc_hess 的 \"auto\" 会在预检的 effective.roles.<role>.hessian_resolution.recalc 里"
        "投影成具体间隔（reason=auto|explicit_interval|explicit_off|symbols_required）；"
        "opt_initial_hessian 只报 requested（没有元素分级解析器），不要拿 5 和 auto 去猜。\n"
        "4. 核对每项电荷/多重度、优化/频率/单点级别、温度与资源。单点级别写在 batch_roles.<role>."
        "sp_method/sp_basis（每 role 一份，ts 与 int 各自生效，ts 不会继承 int 的 sp_*）；顶层 "
        "single_point_method/single_point_basis（或 sp_method/sp_basis 别名）只是 int role 的镜像，"
        "缺 int role/字段会被 422（rule=missing_role_mirror）而不是静默丢弃。\n"
        "5. 预检、提交与等待；读取逐 item/stage 的成功、失败、跳过及原因。\n"
        "6. 对成功优化结果读取适用频率和模式验证；报告原生判定及缺项。\n"
        "7. 只对可比的物种/方法/能量定义/环境做比较；区分电子能与热化学校正，不重复加校正。\n"
        "8. 失败项先判断可修复原因，再在剩余范围与预算内补算；复用已完成阶段的身份校验机制。\n"
        "9. TS 研究若缺模式关联或 IRC/端点证据，继续适用验证或明确停在候选阶段。\n\n"
        "提交约束（机器可读）：single_flat 布局下每个 scheduler task 恰好一个选中结构；"
        "两个候选必须拆成两个 BatchOptimize 任务，各自保留候选来源 revision/role（from_artifact 引用上游 "
        "RESULT/result_manifest.json 的已提交版本）。每个意图生成一次幂等键并重试复用；两个有意重复的计算"
        "使用不同幂等键。task_id 与 job_id 是兼容别名，记录引用时保存带 attempt 的 TaskRef。\n\n"
        "输出：逐候选来源、角色、实际阶段、结构/结果引用、科学判定与缺项、失败恢复、下一验证任务或停止原因。"
    ),
    coverage="first_batch",
    topics=(
        "topic.stationary_point_verification",
        "topic.method_energy_comparability",
        "topic.partial_failure_recovery",
    ),
    version="1.4.0",
)

_PESSEARCH = _workflow_entry(
    "workflow.PESsearch",
    "PESsearch — 键长势能面探索与初猜生成",
    "键长扫描默认入口：探索曲线、提出可追溯 TS/INT 初猜，交由 BatchOptimize 优化验证。",
    (
        "可从结构或上游结果进入扫描；direct bond-length scan 请求包含来源、坐标及协议，执行松弛扫描、"
        "配置的逐帧单点与候选推荐。部分 strategy 名称只参与校验/元数据，不能将 direct_ts 等名称当作自主 "
        "TS 搜索已交付。矩阵反应位点识别算法待接入；首版使用用户提供或确认的位点，缺失时询问。"
        "推荐与最终发布分开，选点仅为初猜。"
    ),
    (
        "当前工作流：PESsearch。键长势能面扫描优先采用此入口。\n"
        "1. 核对用户给定或确认的反应假设、关键原子及结构映射；缺少关键位点则询问，不假称已运行自动位点识别。\n"
        "2. 读取当前可执行的扫描模式和请求契约；核对原子索引约定（0-based）、坐标类型/单位/范围/点数。"
        "坐标 kind 与原子数对应：distance（键长，2 原子，unit=angstrom，start/end>0）、"
        "angle（键角，3 原子，unit=degree，0–180）、dihedral（二面角，4 原子，unit=degree，-360–360）；"
        "start/end 用十进制字符串（JSON float 会被 canonical digest 拒绝），n_points 3–101。"
        "多键扫描：把多个坐标放进 coordinates 数组（最多 64 个，同步扫描、保持拓扑不摊平），"
        "每个坐标独立给出 kind/atoms/unit/start/end/n_points；单坐标用 coordinate（或 coordinates 第一项）。"
        "coordinate 与 coordinates 同时给出时必须完整一致（原子/单位/起止/点数/values），否则提交会被拒绝"
        "（rule=conflicting_fields）——平台不会静默选用其一。double_bond_scan 类选择要求扫描原子对为感知键"
        "（平台限制），因此从产物侧拉伸已成键对更稳妥；bond_stretch 允许任意两原子（平台不强制感知键），"
        "但从反应物侧压缩非键对时曲线中段更易发生分支切换。\n"
        "3. 绑定扫描方法与逐帧能量方法、电子态、资源和预算。大体系例外须记录理由，不自行按原子数拍阈值。\n"
        "4. 执行后先读曲线概况和全部点覆盖状态，再读候选区间的完整邻域点表及必要结构；区分扫描能与单点能，"
        "保留失败点、缺点和不收敛标记。\n"
        "5. 结合目标结构变化、局部曲线和质量信息选择多个有理由的 TS/INT 初猜或明确不选；不得只挑全局最高点。\n"
        "6. 信息不足时提出有限补算区间/坐标与预算，和已有帧初猜提案分开；新点在真实计算完成前不具有帧身份。\n"
        "7. 按真实来源保存提案；全自动授权有效时自动发布，交互模式交由用户审查；缺少自动发布能力时报告未开放，"
        "不借 manual 来源绕过。\n"
        "8. 发布后将初猜连同角色和来源绑定送入 BatchOptimize；保存成功而 Batch 未运行时只报告初猜已保存/待计算。\n"
        "9. 读取 Batch 逐项结果，按适用规则继续模式与端点验证，或保留失败、不确定与停止原因。\n\n"
        "输出：坐标/方法/覆盖、所选初猜与证据、补算记录、发布 revision、Batch 引用及验证进展。"
    ),
    coverage="first_batch",
    topics=(
        "topic.selection_supplement",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
    version="1.2.0",
)

_SCAN = _workflow_entry(
    "workflow.scan",
    "scan — 指定坐标扫描",
    "用户显式选择或确有适用理由的内坐标扫描；ScanTS 显式 opt-in，扫描结束不等于找到驻点。",
    (
        "执行契约支持的内坐标扫描（distance/angle/dihedral），获得逐点结构与能量；"
        "reuse_previous_geometry / retry_count / retry_strategy / failure_policy 是 pointwise scan 的逐点字段，"
        "native scan 会明确拒绝（parameter_effects 登记为 rejected），不要从旧示例照抄；"
        "公开说明以 ORCA relaxed "
        "scan 为主。ScanTS 默认关闭。键长任务默认推荐 PESsearch；scan 用于明确选择或已确认限制的例外。"
        "scan 与 PES 共享底层能力不代表上层候选契约相同。"
    ),
    (
        "当前工作流：scan。执行用户明确选择或确有适用理由的坐标扫描。\n"
        "1. 对键长扫描先检查是否应使用默认 PESsearch；用户明确选 scan 或有已确认限制时记录原因并继续"
        "（典型例外：大体系/多坐标联动/单点成本过高，或 PES 曲线分支切换、点稀疏导致 scan 效果不佳；"
        "scan 点数或范围不足时回到 PES 加密）。不推荐手摆结构当初猜，理由见 topic.selection_supplement。\n"
        "2. 读取 scan 契约：坐标 kind 与原子数对应——distance（键长，2 原子，unit=angstrom，>0）、"
        "angle（键角，3 原子，unit=degree，0–180）、dihedral（二面角，4 原子，unit=degree，-360–360）；"
        "start/end 用十进制字符串，n_points 3–101。多键/多角扫描用 scan_coordinates 数组（多坐标耦合、"
        "同步扫描、保持拓扑），每个坐标独立 kind/atoms/start/end/n_points；单坐标可用 input.coordinate"
        "（字符串紧凑格式 atom_i,atom_j[,atom_k[,atom_l]],start,end 或对象）。scan_coordinates 优先读 input"
        "（input.scan_coordinates/input.coordinate 在 method 之前），不要在 method 里覆盖 input 坐标。"
        "坐标校验要求可确认的原子数（提交显式几何 xyz_text），未开放的维度或后端不猜参数绕过。\n"
        "3. ScanTS 只在明确选择、授权且平台支持时启用；没有启用时不得暗示内部已优化 TS。\n"
        "4. 预检后执行，逐点检查结构可读性、收敛/失败状态和能量可比性；缺失点不插值成已计算事实。\n"
        "5. 目标只是扫描时提供曲线范围、原始结果与局限；目标包含驻点搜索时按编排指南选初猜、必要时在范围内补算。\n"
        "6. 通过 scan 对应的通用帧候选契约发布，不能伪造 PES review；写入口尚未开放时明确说明阻塞。\n"
        "7. 将合法发布的初猜传入 BatchOptimize，并跟踪逐候选优化/频率等结果。\n\n"
        "输出：选择 scan 的原因、坐标与方法、点覆盖及异常、帧引用、初猜/Batch 关联或纯扫描结束理由。"
    ),
    coverage="first_batch",
    topics=("topic.selection_supplement",),
    version="1.2.0",
)

_SINGLEPOINT = _workflow_entry(
    "workflow.singlepoint",
    "singlepoint — 固定几何单点能",
    "在给定几何与电子态上计算电子能等产物；不优化几何、不算热化学。",
    "在给定几何上通过当前工作流的 ORCA 路径计算电子能等所需产物。输入的几何与电子态是科学身份的一部分。",
    (
        "当前工作流：singlepoint。\n"
        "1. 绑定确切结构引用与来源；确认用户要求的是当前几何单点。\n"
        "2. 从契约确认方法、基组或复合方法规则、环境、电荷/多重度和资源；不为带内置基组的方法随意填另一个基组。\n"
        "3. 预检有效配置后提交，检查实际 SCF/电子态质量、执行方法、能量类型和产物完整性。\n"
        "4. 比较结果前核对组成、电子态、方法和能量定义等适用条件；不同化学计量的反应能需明确平衡关系。\n"
        "5. 报告电子能及单位和来源，不把它称为自由能；需要热化学或构象权重时引用相应产物或提出后续任务。\n"
        "6. 未收敛时按明确原因与授权修订，不输出不可信数值作为成功结果。\n\n"
        "输出：结构/方法/电子态、能量及引用、收敛与质量、比较适用范围和后续需求。"
    ),
    coverage="documented_partial",
    topics=("topic.method_energy_comparability",),
)

_OPTIMIZE = _workflow_entry(
    "workflow.optimize",
    "optimize — 几何优化",
    "普通 ORCA 几何优化入口；TS 初猜的批量推进优先走 BatchOptimize 角色配置。",
    "普通 ORCA 几何优化入口；是否存在约束、实际优化语义按当前契约，不能凭方法名称推断。",
    (
        "当前工作流：optimize。\n"
        "1. 确认结构来源、电子态、方法和环境，以及有无约束；属于已发布 PES/scan 初猜的 TS/INT 集合时走 "
        "BatchOptimize 链。\n"
        "2. 读取允许的优化配置与有效默认值，预检后执行；优化方法默认 r2SCAN-3c（绝大多数体系最优），"
        "不推荐 wB97M-V 做几何优化（超大体系可先 GFN2-xTB），见 topic.method_selection；不用未公开 "
        "raw keyword 绕过契约。\n"
        "3. 读取实际优化收敛、SCF 质量、最终几何和轨迹；出现组成/目标结构变化时记录并按目标判断。\n"
        "4. 优化收敛不证明局部或全局最低点；需要驻点性质时继续适用 frequency。\n"
        "5. 有约束的结果保留约束说明；解除约束属于新的明确计算选择。\n"
        "6. 失败只在范围内执行有依据的恢复。\n\n"
        "输出：输入和最终结构引用、实际设置、收敛/变化、驻点验证缺项及下一步。"
    ),
    coverage="documented_partial",
    version="1.2.0",
)

_FREQUENCY = _workflow_entry(
    "workflow.frequency",
    "frequency — 频率与振动分析",
    "在给定结构和方法上运行振动频率计算并发布频率、模式/Hessian 产物；不是优化入口。",
    "在给定结构和方法上运行 ORCA 振动频率计算并发布频率、模式/Hessian 等实际产物；不保证自动给出适用于任意比较的自由能。",
    (
        "当前工作流：frequency。\n"
        "1. 核对几何来源、是否优化及相关约束、电子态、计算方法与所需环境；未优化结构可以按明确目的计算，"
        "但不能暗示它已经是驻点。\n"
        "2. 预检并执行，检查结果完整性、频率/模式对应关系和已有质量判定。\n"
        "3. 对最低点/TS 目标使用平台或已确认的科学判据，不自造统一虚频阈值。\n"
        "4. 目标 TS 不能只凭一个虚频判定正确；需检查模式对应的结构变化，并按需要进一步 IRC/端点关联。\n"
        "5. 热化学量只在实际生成且条件明确时报告；区分电子能与校正项，避免重复累计。\n\n"
        "输出：来源结构/方法、频率和模式引用、质量/判定依据、热化学适用范围、需要的修订或验证。"
    ),
    coverage="documented_partial",
    topics=("topic.stationary_point_verification",),
)

_XTB_OPTIMIZE = _workflow_entry(
    "workflow.xtb_optimize",
    "xtb_optimize — xTB 快速几何优化",
    "GFN-xTB 优化入口，适合授权的预优化/初筛；不等于 DFT 层级结果。",
    "GFN-xTB 等当前支持配置的优化入口。原生约束请求与可选物理单点由专用 XtbConstrainedOpt 入口处理。",
    (
        "当前工作流：xtb_optimize。\n"
        "1. 读取支持的方法/环境/资源和电子态表达，确认用户允许 xTB 级别；超大体系（大几百到上千原子）"
        "的预优化/初筛以 GFN2-xTB 为推荐档（见 topic.method_selection）；不因它快就替代已授权的 DFT 目标。\n"
        "2. 绑定输入结构，使用服务端校验电荷、多重度和所需自旋参数；不为 xTB 硬填 DFT 基组。\n"
        "3. 预检并执行，检查实际收敛、几何变化和能量类型。\n"
        "4. 作为预优化时，将实际最终结构和来源传给后续工作流，保留 xTB 与后续级别的区别。\n"
        "5. 需要专用原生约束/物理单点能力时先发现 XtbConstrainedOpt 是否公开可执行。\n"
        "6. 不将一次快速优化声称为构象搜索完成、全局最低点或 DFT 验证。\n\n"
        "输出：xTB 设置、收敛和结构引用、能量定义、后续用途与限制。"
    ),
    coverage="documented_partial",
    version="1.1.0",
)

_IRC = _workflow_entry(
    "workflow.irc",
    "irc — 从 TS 候选追踪反应路径",
    "从明确 TS 来源的结构运行 IRC，读取各方向轨迹与端点信息；方向符号不自动代表反应物/产物。",
    "从具有明确过渡态来源的结构运行 IRC，读取实际完成的方向/轨迹与端点信息。已知 TS 的路径验证不同于自主发现机理。",
    (
        "当前工作流：irc。\n"
        "1. 来源三件套（**不要手写 ts_source**）：\n"
        "   · input.source_job_id：产出该 TS 结构的已完成作业（BatchOptimize 的 TS item 或 tsmode）。\n"
        "   · input.source_product_id：该作业 result_manifest 里的结构产品 id（Batch 的 TS 产品形如 "
        "batch_<item>，其路径/标签含 __TAG_TS__）。\n"
        "   · input.directions：forward / reverse / both（默认双向）。\n"
        "   ts_source 是平台从上述两项**派生**的 provenance（schema irc_ts_source_v1），会写回 spec 做来源锁定；"
        "**不要**在提交里手写它。若确实带了完整 provenance，平台只做「与派生值是否一致」的校验；"
        "带半份会被 422（rule=irc_ts_source_partial）。CLI 的 --ts-provenance 是人工/历史路径，MCP 不要用。\n"
        "   真实 TS 校验在提交时完成（源作业 completed + 恰一个虚频 + 模式证据），预检只做静态检查并把 "
        "source_reference 投影成 runtime_derived。核对输入的 TS 几何、input role、频率/模式证据与 Hessian 需求；"
        "没有证据时不伪造 TS 来源。\n"
        "2. 来自 PES/scan 的初猜先取得 BatchOptimize 后的相应结构和验证产物。\n"
        "3. 明确目标反应、预期端点、原子映射、方法/电子态、要追踪的方向与资源范围。\n"
        "4. 预检后执行，分别读取各方向进度、终止原因、轨迹与端点；单方向失败不能写成双向连接已确认。\n"
        "5. 读取平台端点分类与结构关联证据；IRC 末帧不自动等同独立确认的最低点。\n"
        "6. 端点与目标不符则记录 refuted 或未定结论，保留轨迹。\n\n"
        "输出：TS 来源、各方向轨迹/状态、端点证据与映射、目标关联结论和剩余验证。"
    ),
    coverage="documented_partial",
    topics=("topic.stationary_point_verification",),
    version="1.1.0",
)

_TSMODE = _workflow_entry(
    "workflow.tsmode",
    "tsmode — 基于已有虚频模式的定向 OptTS",
    "消费已有频率来源 bundle，沿选择的虚频模式执行定向 OptTS 并按实际配置做最终频率验证。",
    "消费已有频率来源 bundle，验证 Hessian/几何与模式映射，沿选择的虚频模式执行定向 OptTS。不同于对任意结构凭空指定一个模式。",
    (
        "当前工作流：tsmode。\n"
        "1. 获取实际 source bundle，检查源 output、Hessian、几何、方法/电子态及映射状态；必须引用真实来源。\n"
        "2. 读取模式表与振动信息，依据目标结构变化选 source_mode_index；模式原始索引不能用图中排序号替代。\n"
        "3. 不启用 allow_unverified_mapping 绕过源映射问题；公共 MCP 尚不能消费该 bundle 时报告缺失入口。\n"
        "4. 读取优化设置、重试上限及 final_frequency 的生效值，预检后执行。\n"
        "5. 检查优化轨迹、最终结构、频率与模式是否满足目标用途。\n"
        "6. 对源 Hessian 不匹配、模式不可用、失败或质量不足，按原因停止/修订。\n"
        "7. 得到候选后按需求进入 IRC/端点验证。\n\n"
        "输出：源 bundle/模式引用、映射状态、实际优化与验证结果、候选用途和后续验证。"
    ),
    coverage="documented_partial",
)

_CASSCF = _workflow_entry(
    "workflow.casscf",
    "casscf — CASSCF / 可选 NEVPT2 单点",
    "ORCA 多参考单点；活性空间、目标态与初猜是科学选择，不从原子数机械猜出。",
    "按实际配置执行 CASSCF 和可选的 NEVPT2 处理。完成检查包括真实 CAS 收敛及请求产物完整性。",
    (
        "当前工作流：casscf。\n"
        "1. 读取结构、电子态、活性电子/轨道数、轨道选择或初猜来源、目标根/态平均设置、方法/基组和所需 "
        "NEVPT2 类型。\n"
        "2. 关键活性空间或态选择缺失时集中向用户请求；不把模型推测当成确定设置。\n"
        "3. 明确这是给定几何单点；不宣称内部自动完成几何优化。\n"
        "4. 预检实际支持范围并执行；读取 CAS 收敛事实、请求产物、能量类型和实际目标态。\n"
        "5. 只在确实请求并成功得到对应产物时报告 NEVPT2 结果，区分 CASSCF 能量与后处理能量。\n"
        "6. 比较多个结构/态前确认活性空间和状态定义可比较。\n\n"
        "输出：活性空间/态/初猜来源、实际方法、收敛及产物证据、能量和适用局限。"
    ),
    coverage="documented_partial",
)

_NMR = _workflow_entry(
    "workflow.nmr",
    "nmr — NMR 预测、归属与候选比较",
    "候选输入与实验谱处理：构象/筛选/GIAO/平均/归属缩放与 DP4/DP5 分析；统计最高值不是无条件结构证明。",
    "候选输入和实验谱处理，经配置的构象生成/筛选、GIAO 屏蔽、Boltzmann/等价核平均、归属/缩放和 DP4/DP5 分析。"
    "具体协议与资产可用性必须发现。",
    (
        "当前工作流：nmr。\n"
        "1. 核对候选结构/立体化学、实验核种与峰值/单位、溶剂/温度、归属状态及缺失信息；不虚构实验峰或候选集合。\n"
        "2. 读取可用协议、误差模型与必要资产/软件；缺资产不能静默输出另一种分析却保持原标签。\n"
        "3. 预检后运行，逐候选检查构象集合、计算成功集合、平均权重实际覆盖和缺失核/峰；部分失败必须报告。\n"
        "4. 使用平台给出的归属、缩放、证据门和统计结果；区分 DP4 的比较语义与 DP5 的实际计算模式。\n"
        "5. 报告模型模式、fallback/诊断和敏感性标记；requires_review 或证据不齐不能隐藏。\n"
        "6. 修改峰值/归属后优先使用已公开且适用的分析修订入口。\n\n"
        "输出：候选与实验版本、实际计算/分析协议、覆盖/缺失、匹配和统计引用、局限与下一步。"
    ),
    coverage="documented_partial",
)

_XTBPATH = _workflow_entry(
    "workflow.XtbPathSearch",
    "XtbPathSearch — 冻结请求的 xTB PATH 探索",
    "消费 pes2ts_xtb_path_request_v1 执行 xTB PATH；本地专用，路径高点不是已验证 TS。",
    "消费冻结请求，经任务层执行 xTB PATH 并发布路径帧/profile。配方由上游拥有，适配器验证和转发而不补科学默认值；"
    "当前为本地专用，不在远程允许集。",
    (
        "当前工作流：XtbPathSearch。\n"
        "1. 确认当前会话确实开放此工作流及冻结请求消费方式；当前本地专用，不能自动投向 LSF。\n"
        "2. 获取真实 pes2ts_xtb_path_request_v1；起终点、原子顺序/映射、电子态、path.inp/recipe、种子、资源与预算"
        "由已认可上游或明确用户方案提供。\n"
        "3. 不根据两张结构图片自行补完整 PATH 配方；缺少冻结内容则报告缺项。\n"
        "4. 校验端点身份、原子映射与请求版本，预检后执行。\n"
        "5. 读取真实路径帧、能量定义、profile 和失败/质量信息；只有实际存在的帧才能用于选初猜。\n"
        "6. 路径高点不是已验证 TS；若目标继续寻找驻点，检查该来源是否支持合法候选发布与 Batch 适配。\n\n"
        "输出：冻结请求来源、端点映射、路径/质量引用、初猜与下游适配状态。"
    ),
    coverage="documented_partial",
)

_ORCAGRADIENT = _workflow_entry(
    "workflow.OrcaGradient",
    "OrcaGradient — 单几何能量梯度",
    "消费 pes2ts_orca_gradient_request_v1 执行 ORCA EnGrad；梯度是 dE/dX（Hartree/bohr），不等于力。",
    "在冻结几何与配置上得到可被上游算法消费的梯度证据。梯度当前产物定义为 Hartree/bohr；只有具备受审查的转换与"
    "坐标绑定时才能用于相应算法。当前本地专用。",
    (
        "当前工作流：OrcaGradient。\n"
        "1. 确认本地入口、公开请求 schema 及当前授权，获取真实 pes2ts_orca_gradient_request_v1。\n"
        "2. 绑定几何、原子顺序、电荷/多重度、方法/基组和请求片段；不擅自改变上游算法指定的计算设置。\n"
        "3. 预检并执行，检查实际能量、梯度维度、有限数值、单位和原子对应；缺失梯度不填零或由模型估计。\n"
        "4. 保持 dE/dX 的语义和符号；不把返回数组直接称为力。\n"
        "5. 本工作流不自动优化结构、不执行完整 TS 搜索、不提供 Hessian。\n"
        "6. 将结果和来源交回已授权上游算法。\n\n"
        "输出：请求/结构引用、实际方法、梯度定义/单位/质量、能量及适用消费者。"
    ),
    coverage="documented_partial",
)

_XTBCONSTRAINED = _workflow_entry(
    "workflow.XtbConstrainedOpt",
    "XtbConstrainedOpt — 原生约束优化与可选无约束单点",
    "消费 pes2ts_xtb_constrained_opt_request_v1；正常退出与几何收敛分开，约束总能与物理单点能区分。",
    "在单个 xTB 进程内执行约束优化，可选在最终几何上做无约束物理单点。约束优化总能含 restraint potential，"
    "必须与 physical_sp 能量区分。当前本地专用。",
    (
        "当前工作流：XtbConstrainedOpt。\n"
        "1. 确认本地支持和冻结请求消费入口，获取真实 pes2ts_xtb_constrained_opt_request_v1；核对原子顺序、"
        "约束/固定原子、目标值、电子态、方法和资源。\n"
        "2. 检查上游指定的 recipe 与 physical_sp 选择；需要物理单点能时明确请求，不将约束总能改名为物理能。\n"
        "3. 预检后执行；不由 harness 在外部接管每步梯度迭代或改写约束。\n"
        "4. 读取原生 evidence 中的 converged_geometry、最终几何、约束信息和错误；workflow completed 但几何未收敛"
        "时明确记录。\n"
        "5. 分别报告 energy_effective 与实际生成的 energy_physical。\n"
        "6. 约束结构不是无约束驻点；继续研究时按公开适配进入 BatchOptimize 与后续验证。\n\n"
        "输出：冻结约束来源、实际收敛、最终结构、两类能量的存在性/定义、下游用途与局限。"
    ),
    coverage="documented_partial",
)

# ---------------------------------------------------------------------------
# Orchestration prompt (plan §14)
# ---------------------------------------------------------------------------

_ORCHESTRATION = GuideEntry(
    guide_id="orchestration.pes_to_batch",
    kind="orchestration",
    title="全自动初猜选择 → BatchOptimize 跨工作流编排",
    version="1.0.0",
    summary="从扫描/路径证据提出初猜，自动推进 BatchOptimize，并给出有证据边界的研究结果。",
    coverage="first_batch",
    sections={
        "body": (
            "当前目标：在用户授权范围内，从扫描/路径证据提出初猜，自动推进 BatchOptimize。\n"
            "阶段 A：核对任务与执行范围——读取反应假设、用户给定/确认的位点、已存在任务和来源结构；核对服务端"
            "验证的自动执行范围、方法/资源、候选数、补算与重试轮次和剩余预算；键长扫描优先 PESsearch，已有用户"
            "显式选择或已确认例外则使用适用 scan 并记录理由；不重跑已有效完成的扫描。\n"
            "阶段 B：读取并判断已有数据——先获取点覆盖、方法/能量定义、失败/缺失区域和实际帧身份，再读候选区间"
            "邻域和必要结构；根据反应假设、结构变化、局部曲线和质量证据提出初猜，保留多个合理备选；证据不够时"
            "选择读取更多数据、有限补算或停止，不强制选最高点。\n"
            "阶段 C：发布与 Batch 提交——保存带 source task/attempt、profile digest、帧 ID、用途角色、证据/理由"
            "和备选的提案；全自动授权有效时按公开操作发布并记录 agent 来源和平台授权依据，不伪装 manual；以合法"
            "发布的候选和稳定 selection revision 构造 BatchOptimize，读取角色实际配置与 profile；预检并用持久提交"
            "意图提交，提交回包丢失先查询原意图/TaskRef。\n"
            "阶段 D：逐候选验证与停止——读取逐 item/stage 结果，保留从初猜到最终几何的关联；用实际优化、频率、"
            "模式和适用端点证据判断；只有选点/发布成功时状态仍为初猜，只有 Batch 完成时也不能直接声称目标反应 "
            "TS 已验证；后续 IRC/端点验证在范围内且能力可用时继续，缺能力/证据、预算耗尽、全部候选不适用或命题"
            "被证伪时停止并解释。\n"
            "恢复时从任务、选点 revision、提交意图和已完成产物继续；来源 rerun 后不把新帧替换进已冻结的旧 Batch。\n\n"
            "最终报告：探索范围、所选初猜及依据、自动授权/发布记录、Batch 引用和逐候选结果、科学结论及缺项、"
            "实际补算/重试与停止原因。模型理由不计作科学证据。"
        ),
    },
    topics=(
        "topic.selection_supplement",
        "topic.stationary_point_verification",
        "topic.partial_failure_recovery",
    ),
)

# ---------------------------------------------------------------------------
# Shared topics (plan §4.1 common topics; one level of indirection)
# ---------------------------------------------------------------------------

_TOPIC_INPUTS = GuideEntry(
    guide_id="topic.inputs_electronic_state",
    kind="topic",
    title="输入与电子态确认",
    version="1.1.0",
    summary="结构引用、原子映射与电荷/多重度是科学身份的一部分。",
    sections={
        "body": (
            "复用已存在的结构、任务和工件；核对项目、来源 attempt、generation/digest 与电子态，保留原子顺序和"
            "映射。不猜绝对路径、不手填摘要、不把展示序号当原子/帧身份。电荷与多重度必须与电子数一致，由平台"
            "校验；改变电荷/电子态属于新的科学选择，需明确记录。SMILES 等输入的原子映射以服务端物化结果为准。\n\n"
            "输入载体按工作流不同，字段名不是同一个概念（照表写，不要跨工作流套用）：\n"
            "· PESsearch：input.source 是**对象**（source_type + xyz_text/asset/artifact、charge、multiplicity），"
            "扫描坐标在 coordinate/coordinates。\n"
            "· scan：input 直接给 xyz_text（或 geometry）+ scan_coordinates/coordinate；不要求 source 对象。\n"
            "· BatchOptimize：input.source_type=\"batch_structures\" + input.items[]（每个 item 带 xyz/role/tag）；"
            "**有 items 就必须写 source_type**，否则提交与预检都会 422（rule=missing_source_type）。"
            "另一条路是 from_artifact/items_file 指向已发布清单（此时不要 items）。\n"
            "· simple 系列（singlepoint/optimize/frequency/xtb_optimize）：input.source_type 是**字符串**"
            "（smiles|xyz_text|structure_asset）+ input.xyz_text / input.input / smiles 之一。\n"
            "· tsmode/IRC 等 step 工作流：input 用 source_job_id + source_product_id（+ 工作流特有字段）；"
            "IRC 的 ts_source 由平台派生，不要手写。\n"
            "枚举值以各工作流契约里的 properties 为准；契约的 input.source_shape 字段标出该工作流用的是哪一种载体。"
        ),
    },
)

_TOPIC_COMPARABILITY = GuideEntry(
    guide_id="topic.method_energy_comparability",
    kind="topic",
    title="方法与能量可比性",
    version="1.1.0",
    summary="只对可比的物种/方法/能量定义/环境做比较。",
    sections={
        "body": (
            "区分扫描能、单点能、电子能与热化学校正，避免重复加校正；不同方法/基组/环境的能量不直接排序；精修/"
            "未精修、xTB/DFT 混合集合不能当同一级别比较。比较反应能前确认化学计量平衡关系与标准态/温度一致。"
            "requested、effective 与 executed 方法身份分别读取；来源不明的值不参与比较。\n"
            "跨方法/跨级别只比**相对值或能差**：绝对总能会随方法系统性偏移（例如 DFT 总能可能低于 "
            "DLPNO-CCSD(T)，这**不是**解析错误）；同一个 log 里 Electronic energy、E(0)、E(TOT)、(T) 与 "
            "FINAL SINGLE POINT ENERGY 是不同的量，报告时必须写明取的是哪一个。报告能量时同时给方法/基组/"
            "零点/标准态/温度，否则不可比。\n"
            "命名类判断（exo/endo、E/Z、R/S、构象编号）必须在计算前用独立标识符钉死（InChIKey/CAS/文献），"
            "几何直觉不是证据；数值巧合（与文献差值很小）也不能反过来证明标签正确。"
        ),
    },
)

_TOPIC_STATIONARY = GuideEntry(
    guide_id="topic.stationary_point_verification",
    kind="topic",
    title="驻点验证",
    version="1.0.0",
    summary="优化收敛不证明驻点性质；TS 判定需要虚频模式对应与端点证据。",
    sections={
        "body": (
            "优化收敛不证明局部最低点，更不证明全局最低点；分类驻点需要频率证据。对 TS 目标：一个虚频只是局部"
            "曲率事实，需检查模式对应的结构变化，并按需要 IRC/端点关联；单方向 IRC 失败或步数耗尽不写成双向连接"
            "已确认。选点/发布成功仍是初猜；Batch completed 不等于反应路径验证完成。缺验证保留 unknown/"
            "inconclusive，证伪保留 refuted。"
        ),
    },
)

_TOPIC_RECOVERY = GuideEntry(
    guide_id="topic.partial_failure_recovery",
    kind="topic",
    title="部分失败与恢复",
    version="1.0.0",
    summary="逐 item/stage 定位失败；只在授权与预算内有依据地补算。",
    sections={
        "body": (
            "区分整个任务与逐 item/stage 的结果；部分失败定位到具体 item/stage 与原因，不整批盲重跑。先判断可修复"
            "原因（输入/收敛/资源/超时），再在剩余范围与预算内补算；复用已完成阶段的身份校验机制，避免相同结构和"
            "配置重复提交。会话恢复先查询平台记录（任务、选点 revision、提交意图），不凭聊天摘要重建；提交回包丢失"
            "先用幂等键查询原 TaskRef，不生成新键。"
        ),
    },
)

_TOPIC_SELECTION = GuideEntry(
    guide_id="topic.selection_supplement",
    kind="topic",
    title="选点与补算",
    version="1.1.0",
    summary="选点只产生初猜；初猜优先来自扫描/路径证据（不推荐手摆）；补算是新点提案，不混同已有帧。",
    sections={
        "body": (
            "PES/scan 中选出的 TS/INT 角色表示后续计算用途，不是已验证驻点；允许多个初猜、平坦区间或无可信候选，"
            "不只挑全局最高点。已有帧初猜提案绑定来源 task/attempt、profile digest、帧 ID、用途角色与证据引用；"
            "补算提案针对目标区间/坐标，通过新计划预检与提交后，用真实新帧选择——新点在计算完成前不具有帧身份。"
            "两种提案分开保存，不互相伪装。第一版可复用已有算法推荐作为线索，但推荐不是确认。"
            "初猜来源优先级：扫描/路径证据优先——PES 适合中小体系（同一键长轴多点、曲线连续）；"
            "大体系、多坐标联动或 PES 曲线不可信/点稀疏时 scan（逐点优化）更合适；已有冻结路径时用路径帧。"
            "不推荐手工摆放结构：手摆初猜没有能量/路径证据，只能作为无证据输入，必须在 BatchOptimize/频率/"
            "IRC 中补足验证，不能直接发布为“已确认候选”。"
        ),
    },
)

_TOPIC_METHOD_SELECTION = GuideEntry(
    guide_id="topic.method_selection",
    kind="topic",
    title="方法与精度阶梯（泛函/基组选择）",
    version="1.4.0",
    summary=(
        "几何优化默认 r2SCAN-3c（绝大多数体系最优，不推荐 wB97M-V 优化；超大体系可 GFN2-xTB 预优）；"
        "单点默认 wB97M-V；wB97X-D4 不并列；CCSD(T) 仅高精度单点。"
    ),
    sections={
        "body": (
            "按精度需求选级别，不无脑上高阶方法（高阶方法成本急剧上升且对几何通常无必要）：\n"
            "1. 预筛/构象/初猜：GFN2-xTB（xtb_optimize、Confsearch 纯 xTB 协议、PES scan_optimizer）。\n"
            "2. 几何优化：**默认 r2SCAN-3c**（复合方法，自带基组，不填 basis）——对绝大多数体系它就是"
            "最优选择，不需要为『更高精度』换泛函。**不推荐用 wB97M-V 做几何优化**：其收益不足以抵消成本，"
            "且会改变与 3c 几何的可比性。\n"
            "   · 常用档：r2SCAN-3c（默认）。\n"
            "   · 很便宜的替代：B97-3c（成本更低、几何质量仍优于 xTB；仅在大体系/探索性优化时）。\n"
            "   · 超大体系（大几百到上千原子）：GFN2-xTB（xtb_optimize；预优化/初筛；不等于 DFT 级），"
            "必要时再用 3c 在关键结构上收敛。\n"
            "   · 只有用户明确要求、或要复现历史记录时，才在优化上用 wB97M-V；须在请求里记录理由。\n"
            "   单点默认 wB97M-V + def2-TZVP(D)（小体系可 wB97M-V/def2-TZVP；超大体系单点按可用资源降档）。"
            "wB97X-D4 只在用户明确要求或历史记录兼容时使用，不作为并列默认（其表现通常不如 wB97M-V）。"
            "能量比较与频率验证在此档完成即可满足多数研究。\n"
            "3. 高精度档（仅在关键量需要 ~1 kcal/mol 级精度时）：在 DFT 几何上做 DLPNO-CCSD(T) 单点"
            "（必要时配更大基组/外推）；不做 CCSD(T) 优化，不把 CCSD(T) 当默认或『更保险』的选择。\n"
            "4. 表达：优化级别与单点级别分开填——BatchOptimize 的 batch_roles.<role> 中 method=优化方法、"
            "sp_method=单点方法（每 role 一份；顶层 single_point_* 只是 int role 的镜像，缺 int role 会被 "
            "422 missing_role_mirror）；singlepoint 工作流只算单点级别。\n"
            "5. 同一比较集内级别必须一致；升级级别属于新的科学决定，须记录理由并重新预检。"
        ),
    },
)

_TOPIC_ARTIFACT_LOCATING = GuideEntry(
    guide_id="topic.artifact_locating",
    kind="topic",
    title="计算产物定位与读取",
    version="1.4.0",
    summary=(
        "status 的 artifacts 是「描述符」（不是 ArtifactRef）；读文件必须 prepare→read（五元组来自 prepare 回包）；"
        "附结果字段地图、任务/目录定位与状态过滤口径。"
    ),
    sections={
        "body": (
            "1. 两条链要分清（这是最容易踩的坑）：\n"
            "   · acp_status 的 artifacts 数组是**描述符**：resource_id、relative_path、kind、label、"
            "size_bytes、prepared、attempt，外加一个现成的 prepare 调用模板。它**没有** generation/digest，"
            "**不能**直接交给 acp_read_artifact。\n"
            "   · acp_read_artifact 要的是**ArtifactRef 五元组**（task_id、attempt、artifact_id、generation、"
            "digest）。它只能来自 acp_prepare_artifact 的回包（artifact_ref 字段），或来自提交/prepare 的"
            "操作回执——不要自己拼。\n"
            "   标准三步：acp_status 取描述符 → acp_prepare_artifact({job_id, purpose:\"artifact\", "
            "artifact_id: <描述符.resource_id>}) 等到 status=ready → acp_read_artifact({artifact_ref, "
            "max_bytes?, encoding?})。descriptor.resource_id 就是 prepare 的 artifact_id；"
            "read 永不触发 prepare/fetch，prepare 永不启动科学计算。\n"
            "2. 目录与目录名：submit 的 molecule_name / task_name 决定 run_root 下的目录名（不传则 "
            "mol_<Workflow>__<序号>）。**目录名不是唯一标识**：失败作业的目录可能被清理后由后续作业重新分配，"
            "同名目录可以属于不同任务。一律用 task_id（= <job_dir>/task.json 的 task_id）交叉验证；status 回包"
            "若给出 directory_owner_task_id 与本作业不一致，以 task_id 为准并视为目录冲突。\n"
            "3. 多作业定位：acp_list_jobs 按状态过滤/分页。状态是**精确匹配**，且活动态不止 running："
            "starting/pending 也是尚未 running 的活动态；status_group=active|terminal|running_now 用来按族过滤。"
            "回包 counts 是**过滤前**的全量计数（10 个状态键恒定），jobs 才是过滤结果——空 jobs 不等于没有作业。"
            "job_id（YYYYMMDD_HHMMSS_NNN_<name>）与 operation_id 不是同一个 id，acp_status 只认 job_id；"
            "未知 id 会返回 NOT_FOUND（不可重试），先用 acp_list_jobs 或 task.json.task_id 核对。\n"
            "4. 结构帧类产物（PES profile/帧、Batch 结构/频率）优先经描述符读取；MCP 面不暴露宿主绝对路径，"
            "需要落到文件时由用户/客户端在 read 后自行保存。\n"
            "5. 工作脚本与中间几何放持久工作区（如仓库工作目录），不要放 /tmp——会话环境可能被清理。\n"
            "6. 可见性：local-single（单用户本机）的 MCP 响应会给出 work_dir、directory_name、name、"
            "idempotency_key 原文，便于日志关联；API-key/多租户模式仍然打码，不要假设两者一样。"
        ),
        "result_fields": (
            "结果字段地图（路径相对 RESULT/；先读 acp_status 的 artifacts 描述符，不要猜路径）：\n"
            "· 频率（frequency 工作流）：RESULT/frequencies/normal_modes.json，schema normal_modes_v1，"
            "频率在 modes[].frequency_cm1（没有顶层 frequencies 数组）；result_manifest.json 的 "
            "frequency_modes 产品是同一数据的简写。\n"
            "· 频率（BatchOptimize）：每个 item 的 RESULT/result_manifest.json → structure 产品的 "
            "metadata.frequency.frequencies（cm^-1 列表）与 frequency_status；逐模细节在 "
            "RESULT/frequencies/<item>__normal_modes.json 的 modes[].frequency_cm1。\n"
            "· step_result.json：内部/恢复用的 step 级数组，不是规范读数面。\n"
            "· 相对能量：PES 曲线读 RESULT/pes_search/pes_profile.json 的 "
            "energy_profile.relative_energies_kcal_mol（相对该曲线最优点）；Batch/优化只存逐 item "
            "原始 energy_hartree，相对能量按同一 method/profile 在读取时计算，不要手拼不同级别能量。\n"
            "· IRC：轨迹在 RESULT/irc/irc_<direction>_path.xyz 与 RESULT/trajectories/irc_trajectory.json"
            "（frames/directions）；"
            "formal endpoint 只来自收敛确认的端点文件，last_observed_geometries 仅是诊断证据，不能当端点。\n"
            "· 单点产物落盘位置（同一个 sp.out 文件名，两条历史布局，都在 job_dir 下）：singlepoint 工作流写 "
            "<job_dir>/sp.out；BatchOptimize（single_flat 布局）的逐 item 单点写 <job_dir>/WORK/05_SP/sp.out。"
            "通用解析器两处都要认；读数仍以 RESULT/result_manifest.json 的产品路径为权威，路径只用于人工核对，"
            "不要按文件名猜。\n"
            "· 热化学（ZPE/H/G/S）：频率作业走 RESULT/thermochemistry.json，BatchOptimize 每个 item 走 "
            "RESULT/thermochemistry/<item_id>.json；描述符 kind=thermochemistry，含温度/压力/相校正/来源。"
            "**opt_freq_sp 这类「优化+频率+单点」profile 只要频率步成功就会登记该产品**（不要求 Shermo 热化学步）；"
            "直接读产品，不要 grep freq.out 的 Final Gibbs free energy。产品只在热化学实际产出时登记"
            "（无频率/失败/旧作业则没有该产品，此时才需要看频率步的 log，且仍是最后一条 "
            "FINAL SINGLE POINT ENERGY）。\n"
            "· 失败原因：TS/优化失败的消息形如 failed [<class>]: <summary>（class 如 "
            "hessian_not_positive_definite / opt_maxiter / geometry_not_converged / error_termination；"
            "SCF 类为 scf_failure（旧写法）或 scf_not_converged），摘要 ≤200 字符且不含宿主绝对路径；"
            "完整结构在 metadata.failure_details（reason_lines/scf_cycles/opt_cycles/imaginary_count/"
            "terminal）。Batch 逐 item 的 rescue 细节在 RESULT/batch_provenance.json 的 "
            "rescue_failure_details（该文件不是 manifest 产品，不在 artifacts 里）；agent 可见的失败文案是"
            "structure 产品的 failure_reason 与 job error。unknown 表示确实未识别，不是平台故障。\n"
            "· Hessian 成本可见性：hessian_recomputed / hessian_source（opt_initial = 优化开头算的，"
            "freq_numfreq = 频率步重新算的）出现在 item 的 step_result.json metadata、structure 产品 "
            "metadata，以及运行期 live_status.metrics（key=hessian_source）；当前频率步默认重算，"
            "这是已知成本，不要误以为平台漏算了。\n"
            "· 何时走 prepare→read：任何 read 都要先 prepare（五元组只能来自 prepare 回包）；本地已 prepare "
            "过的产物会走快速路径直接回 ready + artifact_ref，远端/冷缓存则排队 prepare；read 永不触发 "
            "fetch/prepare。"
        ),
    },
)

_TASK_FIND_TS = GuideEntry(
    guide_id="task.find_transition_state",
    kind="task",
    title="任务：寻找反应过渡态",
    version="1.2.0",
    summary=(
            "从结构+反应假设到 TS 候选：选初猜来源（PES 默认；大体系/多坐标用 scan；不推荐手摆）→ 扫描 → "
            "选初猜 →（有授权才 publish，否则 inline role:\"ts\" 一等路径直达）BatchOptimize(TS) → 频率判定。"
        ),
    coverage="first_batch",
    sections={
        "when_to_use": (
            "适用：用户要找一个反应（分子内重排、双分子加成、σ 迁移等）的过渡态结构。\n"
            "必需输入：①至少一侧结构（反应物或产物均可）；②反应假设/关键原子（哪些键断裂、哪些键形成）；"
            "③方法/资源授权。缺少反应假设时先询问，不做猜测性位点识别（矩阵位点算法未接入）。"
        ),
        "chain": (
            "0. 初猜生成方式（先决定，不要跳过）：默认 PESsearch 键长扫描（中小体系；同一键长轴多点扫描、"
            "能量曲线连续、单点成本可控）。大体系、需要多坐标联动/耦合、或 PES 曲线分支切换/点稀疏导致"
            "scan 效果不佳时，改用 scan（relaxed/pointwise）逐点优化；scan 的点数/范围不足以支撑判断时"
            "回到 PES 加密扫描。已有冻结路径 payload 时用 XtbPathSearch 路径帧（链路 C）。"
            "不推荐手工摆放结构作为初猜：手摆没有扫描能量/路径证据，平台不禁止，但指南不推荐；"
            "若用户坚持手摆，必须说明其无证据性质，并靠频率（恰一个虚频、模式方向）与 IRC/端点验证补足，"
            "不能当成已确认候选发布。\n"
            "链路 A（扫描，通用）：\n"
            "1. PESsearch（mode=bond_length_scan）——多坐标同步扫描；多键扫描用 input.coordinates 数组"
            "（最多 64 个，同步扫描），键角/二面角用 kind=angle（3 原子）/dihedral（4 原子）、unit=degree、"
            "范围 0–180 / -360–360。布局建议（平台不强制）：从产物侧拉伸已成键对更易得到连续曲线；"
            "反应物侧压缩非感知键对也可提交（bond_stretch 允许任意两原子），但更易分支切换，读曲线按连续段取点；"
            "协议见 workflow.PESsearch。\n"
            "2. 读曲线概况+点覆盖；极大点邻域取候选，关注分支切换（连续段内取点），见 topic.selection_supplement。\n"
            "3. 选点怎么进 Batch（先自查发布授权，再选路径；两条路径都是合法的）：\n"
            "   · 纯 MCP 会话（没有管理员预铸的有界授权）：acp_publish_pes_review 一定会被拒绝——它要求 "
            "authorization_id，且服务端固定 selector=agent，不会退化成 manual。此时的 supported 路径是"
            "把选中的初猜直接内联进 BatchOptimize：input.items 里每个结构显式写 role:\"ts\"（与 xyz 并列），"
            "沿用 batch_roles.ts 配置。inline 路径是一等路径，不是兜底：发布只给候选一个 review revision，"
            "不是进入 Batch 的前提。不要用 probe/自造 authorization_id 试探——工具只回 typed 拒绝"
            "（authorization_not_found：id 未知或跨项目；access_denied：权限/授权被拒），平台不会自动铸造授权。\n"
            "   · 已有合法发布的选择（人工已在 Workbench/REST review 确认，或管理员授权下已发布）：直接 "
            "input.from_artifact 指向该 RESULT/result_manifest.json + selected_ids，消费已确认候选及其角色。\n"
            "   · 管理员已铸造有界授权时，PES → publish → BatchOptimize 完整链可用：POST "
            "/api/v1/selection-authorizations（local-single 的 operator 身份；body 至少含 source_job_id、"
            "allowed_attempts、valid_until、limits:{max_candidates>=1,...}；owner 与 authorization_id 由"
            "服务端生成，不能自授权）→ 带 authorization_id 调 acp_publish_pes_review → 发布后得到 "
            "candidate_id。授权是否可用只以调用结果为准（typed 拒绝即无可用授权），不要假称授权存在。\n"
            "   · 人工确认仍走 Workbench / REST review（selector=manual）；MCP 的 acp_publish_pes_review "
            "恒为受限 Agent 路径，纯 MCP 会话不能替人做 manual 发布。\n"
            "4. BatchOptimize（profile=opt_freq，item 显式 role:\"ts\"）——role 的 wire 语法见 workflow.BatchOptimize；"
            "每任务一个结构（single_flat）。\n"
            "5. 读逐 item 频率：恰一个显著虚频 + 模式对应成键/断键方向 → 见 topic.stationary_point_verification。\n"
            "链路 B（PES/scan 不可用或效果不佳、且用户显式选择时）：scan（relaxed/pointwise）逐点扫描，"
            "多键/多角用 input.scan_coordinates 数组（多坐标耦合），kind=distance/angle/dihedral 分别对应 "
            "2/3/4 原子与 angstrom/degree 单位；按 workflow.scan 的契约与限制；链路 C（上游冻结路径 payload 已存在时）：XtbPathSearch 路径帧 → 同第 3 步起。"
        ),
        "decisions": (
            "决策点：①扫描坐标与初始键距（来源结构决定）；②TS/INT 角色（TS=尝试过渡态优化）；③profile 必须含频率；"
            "④方法级别按 topic.method_selection 的阶梯，不无脑上高阶；⑤进 Batch 的方式（有界授权已就绪走 publish，"
            "否则 inline role:\"ts\" 直达；两条都不跳过 Batch 频率/端点验证）。"
        ),
        "completion": (
            "完成定义：OptTS 收敛 + 恰一个虚频 + 模式方向一致 = TS 驻点 supported；"
            "路径连接性未做 IRC 时保持 inconclusive（不写\"找到反应路径\"）；"
            "Batch completed 不等于验证完成。"
        ),
        "stop": (
            "停止条件：曲线无可信极大/全部候选失败/预算耗尽/候选人角色与目标不符 → 停止并报告（含缺项）；"
            "不通过改角色、改输入或删模式来\"成功\"。"
        ),
        "not_supported": (
            "当前不支持：矩阵反应位点自动识别（待接入，不冒充）；无管理员有界授权的自动发布"
            "（纯 MCP 会话走 inline role:\"ts\" 直达 BatchOptimize，人工确认走 Workbench/REST review）；"
            "irc 提交键未契约化（fallback 见 workflow_contracts 索引）。"
        ),
    },
    topics=("topic.stationary_point_verification", "topic.method_selection", "topic.selection_supplement"),
)

_TASK_VERIFY_PATH = GuideEntry(
    guide_id="task.verify_reaction_path",
    kind="task",
    title="任务：验证 TS 连接反应物与产物",
    version="1.0.0",
    summary="对已判定的 TS 候选做 IRC 双向追踪与端点比对，给出连接性强证据。",
    coverage="documented_partial",
    sections={
        "when_to_use": (
            "适用：已有一个判定为 TS 的候选（有频率/模式证据），需要验证它连接目标反应物与产物。\n"
            "必需输入：TS 几何 + 频率/模式证据（provenance）+ 原子映射 + 方法与电子态。缺 TS provenance 不伪造。"
        ),
        "chain": (
            "1. irc 工作流（input_role=transition_state，带 ts_provenance）：双方向追踪，见 workflow.irc。\n"
            "2. 读各方向状态/终止原因/轨迹/端点；单方向失败不写成双向确认。\n"
            "3. 端点结构 → BatchOptimize（INT 角色）做端点优化与频率确认（可选但推荐）。\n"
            "4. 端点与目标反应物/产物的原子映射比对 → 判定 supported/refuted/inconclusive。"
        ),
        "decisions": (
            "决策点：方向与步数上限（来自契约）；端点是否需要自由优化；是否接受\"端点接近但未优化\"的弱证据（要如实标注）。"
        ),
        "completion": (
            "完成定义：双向都到达终点且端点与目标一致 = supported；单侧/端点不符 = refuted 或 inconclusive，保留轨迹。"
        ),
        "stop": (
            "停止条件：TS provenance 缺失、映射不可靠、端点持续不收敛 → 停止并报告缺项；不重命名端点制造\"连接成功\"。"
        ),
        "not_supported": (
            "当前限制：irc 的 MCP 提交键未契约化（workflow_contracts 索引 fallback：用 CLI 或报告缺口）；"
            "端点分类与目标映射需用户给定反应假设。"
        ),
    },
    topics=("topic.stationary_point_verification",),
)

_TASK_CONFORMER = GuideEntry(
    guide_id="task.conformer_and_energy",
    kind="task",
    title="任务：构象搜索与批量能量比较",
    version="1.0.0",
    summary="Confsearch 生成/筛选 → 候选 → BatchOptimize 逐项优化/频率 → 可比能量比较。",
    coverage="first_batch",
    sections={
        "when_to_use": (
            "适用：给定分子（含立体化学/电荷/多重度），要获得构象集合并按能量排序或继续下游计算。\n"
            "必需输入：结构（SMILES/XYZ/资产）、电子态、协议与筛选/精修策略、资源。"
        ),
        "chain": (
            "1. Confsearch（协议 xtb-crest/xtb-md/censo-crest/xtbmd-censo；screen 允许零精修），见 workflow.Confsearch。\n"
            "2. 读分阶段计数（产生/保留/精修选中/精修成功），不互换。\n"
            "3. 选下游候选（稳定引用+多样性+预算）→ BatchOptimize（profile=opt_freq，item role:\"int\"）逐项优化与频率。\n"
            "4. 能量比较：同一方法/基组/电子态/环境；电子能与热校正分开，不重复加校正（topic.method_energy_comparability）。"
        ),
        "decisions": (
            "决策点：协议（纯 xTB 不宣称 DFT 精修）；精修策略；候选数与多样性；是否继续优化/频率。"
        ),
        "completion": (
            "完成定义：逐候选来源/方法/能量类别可读；混合级别不直接排序；构象搜索完成不证明全局最低点或空间穷尽。"
        ),
        "stop": (
            "停止条件：某阶段失败时区分已有可用集合与未完成部分；只在授权内恢复/补算，不伪称全套协议完成。"
        ),
        "not_supported": "当前限制：无自动去重的 RMSD 分析不得捏造；NMR 专属参数不得用于 Confsearch。",
    },
    topics=("topic.method_energy_comparability", "topic.partial_failure_recovery"),
)

_TASK_BARRIER = GuideEntry(
    guide_id="task.barrier_high_accuracy",
    kind="task",
    title="任务：反应能垒与高精度单点",
    version="1.3.0",
    summary="端点几何/频率确认 → 默认级别单点 → 需要 ~1 kcal/mol 时 DLPNO-CCSD(T) 单点。",
    coverage="documented_partial",
    sections={
        "when_to_use": (
            "适用：已有反应物/产物/TS 几何（最好有频率确认），要给出可比较的反应能垒或热化学量。\n"
            "必需输入：端点与 TS 几何及其电子态、方法授权、比较条件（化学计量/标准态/温度）。"
        ),
        "chain": (
            "1. 端点优化与频率：几何优化默认 r2SCAN-3c（绝大多数体系最优；不推荐 wB97M-V 做优化；"
            "超大体系可先 GFN2-xTB，见 topic.method_selection）+ 频率（优化收敛不证明最小值，频率确认；见 "
            "workflow.optimize/frequency）。\n"
            "2. 默认单点：wB97M-V + def2-TZVP(D)（singlepoint；复合方法不填 basis；wB97X-D4 仅在用户"
            "明确要求或历史记录兼容时使用，见 topic.method_selection）。\n"
            "3. 仅当关键量需要 ~1 kcal/mol 级精度：DLPNO-CCSD(T) 单点（在 DFT 几何上；不做 CCSD(T) 优化）。\n"
            "4. 能垒组合：TS−反应物（电子能）；需要热化学校正时按实际生成的相位校正规则明确引用，不重复加。"
        ),
        "decisions": (
            "决策点：级别选择（默认 vs 高精度）；端点是否自由优化；ZPE/热校正是否纳入；标准态与温度。"
        ),
        "completion": (
            "完成定义：端点/TS 的来源与级别可追溯；电子能与校正项分开报告；比较前核对化学计量/平衡关系。"
        ),
        "stop": "停止条件：端点未收敛/级别不可比/缺频率校正依据 → 停止并标注缺项，不硬拼能垒。",
        "not_supported": "当前限制：DLPNO-CCSD(T) 优化、CBS 外推与自动标准态换算未在首版配方内。",
    },
    topics=("topic.method_selection", "topic.method_energy_comparability"),
)

_TASK_MULTIREF = GuideEntry(
    guide_id="task.multireference_analysis",
    kind="task",
    title="任务：多参考分析（CASSCF 与 NEVPT2）",
    version="1.0.0",
    summary="近简并/双自由基场景的多参考单点：活性空间须用户/专家决定，CASSCF 与 NEVPT2 能量分开报。",
    coverage="documented_partial",
    sections={
        "when_to_use": (
            "适用：近简并电子态、双自由基/开壳层单重态、单参考 DFT 明显不可信、需要定性/定量多参考分析。\n"
            "必需输入：结构 + 电子态（电荷/多重度）、活性空间（电子数/轨道数或初猜来源）、目标根/态平均设置、"
            "是否 NEVPT2。**活性空间与态选择是科学决定，缺失时向用户询问，不做机械猜测。**"
        ),
        "chain": (
            "1. casscf 工作流：给定几何的单点（不自动优化），见 workflow.casscf。\n"
            "2. 读 CAS 收敛事实与请求产物：CASSCF 能量、目标态、若请求的 NEVPT2。\n"
            "3. 与单参考结果对比时确认活性空间/态定义一致；异常根变化先报告，不自动改活性空间凑数。"
        ),
        "decisions": (
            "决策点：活性空间大小与轨道选择（用户/专家）；态平均与根跟踪；是否/哪种 NEVPT2；与 DFT 结果如何并列。"
        ),
        "completion": (
            "完成定义：CAS 收敛 + 请求产物完整 + 能量类型明确（CASSCF vs NEVPT2 分开）；"
            "缺收敛事实保持 unknown；不做\"进程成功=收敛\"的推断。"
        ),
        "stop": (
            "停止条件：活性空间不明、目标态不可比、CAS 未收敛 → 停止并报告缺项；"
            "不自动扩大/缩小活性空间来获得\"更好数值\"。"
        ),
        "not_supported": (
            "当前限制：自动活性空间选择/自动化多参考诊断未交付；casscf 的 MCP 提交键未契约化"
            "（workflow_contracts 索引 fallback：用 CLI 或报告缺口）。"
        ),
    },
    topics=("topic.method_selection", "topic.method_energy_comparability", "topic.stationary_point_verification"),
)

_TOPIC_NON_STANDARD = GuideEntry(
    guide_id="topic.non_standard_entries",
    kind="topic",
    title="非普通 active 入口（BoundedRecipe / retired / fake）",
    version="1.0.0",
    summary="遇到非普通入口先读取真实公开状态；不从历史名称推断今天可提交。",
    sections={
        "body": (
            "BoundedRecipe 是额外的 envelope-only 配方执行入口，只执行提交前冻结的依赖和参数，不计为第 16 个普通"
            "工作流。retired 工作流仅历史读取，禁止因旧文档提到 ensemble/energy/optfreq 等名称就提交。fake 仅测试，"
            "custom_sequence 为 planned。不得将文档中的步骤列表当编译器已支持的 DAG；不得为补齐缺失步骤执行任意脚本"
            "或改用 fake；旧任务可只读解释，不能从历史 workflow 名称推断它今天仍能提交。"
        ),
    },
)

_ENTRIES: tuple[GuideEntry, ...] = (
    _SHARED_EXECUTION,
    _CONFSEARCH,
    _BATCHOPTIMIZE,
    _PESSEARCH,
    _SCAN,
    _SINGLEPOINT,
    _OPTIMIZE,
    _FREQUENCY,
    _XTB_OPTIMIZE,
    _IRC,
    _TSMODE,
    _CASSCF,
    _NMR,
    _XTBPATH,
    _ORCAGRADIENT,
    _XTBCONSTRAINED,
    _ORCHESTRATION,
    _TOPIC_INPUTS,
    _TOPIC_COMPARABILITY,
    _TOPIC_STATIONARY,
    _TOPIC_RECOVERY,
    _TOPIC_SELECTION,
    _TOPIC_METHOD_SELECTION,
    _TOPIC_ARTIFACT_LOCATING,
    _TASK_FIND_TS,
    _TASK_VERIFY_PATH,
    _TASK_CONFORMER,
    _TASK_BARRIER,
    _TASK_MULTIREF,
    _TOPIC_NON_STANDARD,
)

_GUIDES: dict[str, GuideEntry] = {entry.guide_id: entry for entry in _ENTRIES}


def guide_ids() -> tuple[str, ...]:
    """All guide ids in pack order."""
    return tuple(_GUIDES)


def get_guide(guide_id: str) -> GuideEntry:
    """Return one entry; raises ``KeyError`` for an unknown id."""
    return _GUIDES[guide_id]


def workflow_guide_map() -> dict[str, str]:
    """Active workflow id -> its workflow guide id (catalog-driven check base)."""
    return {
        entry.workflows[0]: entry.guide_id
        for entry in _ENTRIES
        if entry.kind == "workflow" and entry.workflows
    }


def guidance_index() -> dict[str, Any]:
    """Compact discovery index (plan §4.2: cards, not full bodies)."""
    cards = []
    for entry in _ENTRIES:
        card = {
            "guide_id": entry.guide_id,
            "kind": entry.kind,
            "title": entry.title,
            "version": entry.version,
            "coverage": entry.coverage,
            "summary": entry.summary,
            "digest": _entry_digest(entry),
            "size_bytes": _entry_size_bytes(entry),
            "sections": list(entry.sections),
        }
        if entry.workflows:
            card["workflows"] = list(entry.workflows)
        if entry.topics:
            card["topics"] = list(entry.topics)
        cards.append(card)
    return {
        "format": GUIDE_FORMAT,
        "pack_version": PACK_VERSION,
        "max_guide_bytes": MAX_GUIDE_BYTES,
        "guides": cards,
    }


def read_guide(
    guide_id: str,
    *,
    section: str | None = None,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Read one guide (optionally one section) with explicit omissions.

    Args:
        guide_id: Entry id.
        section: Optional single section name.
        max_bytes: Optional byte bound for the returned body.  When the
            selection would exceed it, the payload degrades to section names
            plus byte sizes with ``"omitted_reason": "budget"`` — never a
            silent truncation (plan §4.2).

    Raises:
        KeyError: Unknown guide id.
        ValueError: Unknown section name.
    """
    entry = _GUIDES[guide_id]
    base = {
        "format": GUIDE_FORMAT,
        "guide_id": entry.guide_id,
        "kind": entry.kind,
        "title": entry.title,
        "version": entry.version,
        "coverage": entry.coverage,
        "summary": entry.summary,
        "digest": _entry_digest(entry),
        "topics": list(entry.topics),
    }
    if entry.workflows:
        base["workflows"] = list(entry.workflows)
    if section is not None and section not in entry.sections:
        raise ValueError(
            f"guide {guide_id} has no section {section!r}; available: {list(entry.sections)}"
        )
    selection = {section: entry.sections[section]} if section else dict(entry.sections)
    body_bytes = sum(len(text.encode("utf-8")) for text in selection.values())
    if max_bytes is not None and body_bytes > int(max_bytes):
        return {
            **base,
            "sections": {},
            "section_sizes": {
                name: len(text.encode("utf-8")) for name, text in entry.sections.items()
            },
            "size_bytes": body_bytes,
            "omitted": list(entry.sections),
            "omitted_reason": "budget",
            "hint": "re-read with a larger max_bytes or per-section reads",
        }
    return {
        **base,
        "sections": selection,
        "size_bytes": body_bytes,
        "omitted": [name for name in entry.sections if name not in selection] or None,
        "omitted_reason": None if section is None or section in selection else "section_filter",
    }


def _active_workflow_ids() -> tuple[str, ...]:
    return tuple(
        str(entry["id"])
        for entry in WORKFLOW_CATALOG
        if entry.get("status", "active") == "active" and entry.get("visible", True)
    )


def audit_pack() -> dict[str, Any]:
    """Machine check material: coverage of catalog active workflows (plan §15.1)."""
    active = _active_workflow_ids()
    covered = set(workflow_guide_map())
    return {
        "active_workflows": list(active),
        "guided_workflows": sorted(covered),
        "missing_guides": sorted(set(active) - covered),
        "extra_guides": sorted(covered - set(active)),
        "pack_version": PACK_VERSION,
    }
