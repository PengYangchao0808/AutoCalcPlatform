"""Tests for acp.storage — v2 task-storage layout objects (design doc §3–§5, §8, §9)."""

from __future__ import annotations

import json

import pytest

from acp.storage import (
    MANIFEST_FILENAME,
    TASK_DIR_NAME_MAX_LEN,
    NodePathMapping,
    Product,
    ProductKind,
    ResultManifest,
    TaskLayout,
    TaskRecord,
    TaskStorage,
    is_v2_task_dir,
    sanitize_task_dir_name,
)


class TestSanitizeTaskDirName:
    def test_spaces_become_underscores(self) -> None:
        assert sanitize_task_dir_name("ethanol", "opt final") == "ethanol_opt_final"

    def test_doc_section_4_3_examples(self) -> None:
        name = sanitize_task_dir_name("ethanol", "opt", "r2scan final")
        assert name == "ethanol_opt_r2scan_final"
        name = sanitize_task_dir_name("XXXTS1", "mechanism", "route01")
        assert name == "XXXTS1_mechanism_route01"

    def test_forbidden_chars_replaced(self) -> None:
        for ch in '/\\:*?"<>|':
            assert ch not in sanitize_task_dir_name(f"mol{ch}x", f"ta{ch}sk")
        assert sanitize_task_dir_name("a/b", "c:d") == "a_b_c_d"

    def test_empty_remark_omitted(self) -> None:
        assert sanitize_task_dir_name("ethanol", "opt") == "ethanol_opt"
        assert sanitize_task_dir_name("ethanol", "opt", "   ") == "ethanol_opt"

    def test_whitespace_collapse_and_trim(self) -> None:
        assert sanitize_task_dir_name("  ethanol  ", "\topt\n", "final") == "ethanol_opt_final"

    def test_molecule_and_task_required(self) -> None:
        with pytest.raises(ValueError, match="molecule_name"):
            sanitize_task_dir_name("///", "opt")
        with pytest.raises(ValueError, match="task_name"):
            sanitize_task_dir_name("ethanol", "???")
        with pytest.raises(ValueError, match="molecule_name"):
            sanitize_task_dir_name("", "opt")

    def test_max_length_enforced(self) -> None:
        name = sanitize_task_dir_name("m" * 80, "t" * 80)
        assert len(name) <= TASK_DIR_NAME_MAX_LEN
        assert not name.endswith("_")


class TestTaskLayoutConstants:
    def test_work_stages_match_doc_section_6(self) -> None:
        assert TaskLayout.WORK_STAGES == (
            "00_RUNTIME",
            "01_PREPARE",
            "02_SEARCH",
            "03_OPT",
            "04_FREQ",
            "05_SP",
            "06_THERMO",
            "07_PATH",
            "08_ANALYSIS",
        )

    def test_result_categories_match_doc_section_7(self) -> None:
        assert TaskLayout.RESULT_CATEGORIES == (
            "structures",
            "energies",
            "frequencies",
            "trajectories",
            "ensembles",
            "mechanism",
            "reports",
        )

    def test_defaults_instance(self) -> None:
        layout = TaskLayout()
        assert layout.stage_runtime == "00_RUNTIME"
        assert layout.category_mechanism == "mechanism"
        assert TaskLayout.WORK_DIR_NAME == "WORK"
        assert TaskLayout.RESULT_DIR_NAME == "RESULT"


class TestTaskStoragePaths:
    def test_path_resolution(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "ethanol_opt_final")
        root = tmp_path / "ethanol_opt_final"
        assert storage.work_dir() == root / "WORK"
        assert storage.result_dir() == root / "RESULT"
        assert storage.runtime_dir() == root / "WORK" / "00_RUNTIME"
        assert storage.stage_dir("03_OPT") == root / "WORK" / "03_OPT"
        assert storage.stage_dir("03_OPT", "ORCA") == root / "WORK" / "03_OPT" / "ORCA"
        assert storage.stage_dir("07_PATH", "route01") == root / "WORK" / "07_PATH" / "route01"
        assert storage.result_category_dir("structures") == root / "RESULT" / "structures"
        assert storage.result_category_dir("mechanism") == root / "RESULT" / "mechanism"
        assert storage.input_xyz() == root / "input.xyz"
        assert storage.input_source_json() == root / "input_source.json"
        assert storage.task_json() == root / "task.json"
        assert storage.result_manifest_json() == root / "RESULT" / "result_manifest.json"

    def test_windows_style_task_dir(self) -> None:
        storage = TaskStorage("E:\\ACP_Calculations\\ethanol_opt_final")
        work = storage.work_dir()
        assert work.name == "WORK"
        assert "ethanol_opt_final" in str(work)

    def test_unknown_stage_or_category_raises(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "t")
        with pytest.raises(ValueError, match="unknown WORK stage"):
            storage.stage_dir("99_BOGUS")
        with pytest.raises(ValueError, match="unknown RESULT category"):
            storage.result_category_dir("bogus")
        with pytest.raises(ValueError, match="unsafe engine"):
            storage.stage_dir("03_OPT", "../escape")


class TestEnsureLayout:
    def test_default_creates_only_runtime_and_result(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "task")
        storage.ensure_layout()
        assert (tmp_path / "task" / "WORK" / "00_RUNTIME").is_dir()
        assert (tmp_path / "task" / "RESULT").is_dir()
        work_children = [p.name for p in (tmp_path / "task" / "WORK").iterdir()]
        assert work_children == ["00_RUNTIME"]
        assert list((tmp_path / "task" / "RESULT").iterdir()) == []

    def test_creates_only_requested_dirs(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "task")
        storage.ensure_layout(stages=["02_SEARCH", "03_OPT"], categories=["structures"])
        assert (tmp_path / "task" / "WORK" / "02_SEARCH").is_dir()
        assert (tmp_path / "task" / "WORK" / "03_OPT").is_dir()
        assert not (tmp_path / "task" / "WORK" / "05_SP").exists()
        assert (tmp_path / "task" / "RESULT" / "structures").is_dir()
        assert not (tmp_path / "task" / "RESULT" / "energies").exists()


class TestWriteHelpers:
    def test_write_input_xyz(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "task")
        path = storage.write_input_xyz("3\nxyz\nC 0 0 0\n")
        assert path.read_text(encoding="utf-8") == "3\nxyz\nC 0 0 0\n"

    def test_write_input_source_json(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "task")
        path = storage.write_input_source_json({"source_type": "smiles", "smiles": "CCO"})
        assert json.loads(path.read_text(encoding="utf-8"))["smiles"] == "CCO"

    def test_write_task_json_atomic_no_tmp_left(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "task")
        record = TaskRecord(
            task_id="task_001",
            project_id="proj_001",
            molecule_name="ethanol",
            task_name="opt",
            workflow="optimize",
            task_dir_name="ethanol_opt",
        )
        path = storage.write_task_json(record)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["task_id"] == "task_001"
        assert payload["layout_version"] == 2
        assert not (tmp_path / "task" / "task.json.tmp").exists()


class TestIsV2TaskDir:
    def test_work_dir_marks_v2(self, tmp_path) -> None:
        storage = TaskStorage(tmp_path / "new_task")
        storage.ensure_layout()
        assert is_v2_task_dir(tmp_path / "new_task")

    def test_task_json_layout_version_2_marks_v2(self, tmp_path) -> None:
        task_dir = tmp_path / "json_only"
        task_dir.mkdir()
        (task_dir / "task.json").write_text(json.dumps({"layout_version": 2}), encoding="utf-8")
        assert is_v2_task_dir(task_dir)

    def test_legacy_dir_not_v2(self, tmp_path) -> None:
        legacy = tmp_path / "legacy_job"
        (legacy / "mechanism_study").mkdir(parents=True)
        (legacy / "result_summary.json").write_text("{}", encoding="utf-8")
        assert not is_v2_task_dir(legacy)

    def test_layout_version_1_not_v2(self, tmp_path) -> None:
        task_dir = tmp_path / "old"
        task_dir.mkdir()
        (task_dir / "task.json").write_text(json.dumps({"layout_version": 1}), encoding="utf-8")
        assert not is_v2_task_dir(task_dir)

    def test_missing_or_invalid(self, tmp_path) -> None:
        assert not is_v2_task_dir(tmp_path / "nonexistent")
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "task.json").write_text("not json", encoding="utf-8")
        assert not is_v2_task_dir(broken)


class TestResultManifest:
    def test_round_trip(self, tmp_path) -> None:
        result_dir = tmp_path / "RESULT"
        manifest = ResultManifest(task_id="task_001", workflow="optfreqsp", status="completed")
        manifest.add_product(
            "optimized_structure", "优化后结构", "structures/optimized.xyz", ProductKind.STRUCTURE
        )
        manifest.add_product(
            "frequency_modes", "振动模式", "frequencies/normal_modes.json", "frequency_modes"
        )
        manifest.add_product(
            "energy_summary", "能量汇总", "energies/energy_summary.json", "energy_report"
        )
        path = manifest.write(result_dir)
        assert path == result_dir / MANIFEST_FILENAME

        loaded = ResultManifest.read(result_dir)
        assert loaded.version == 2
        assert loaded.task_id == "task_001"
        assert loaded.workflow == "optfreqsp"
        assert loaded.status == "completed"
        assert len(loaded.products) == 3
        assert loaded.products[0].kind is ProductKind.STRUCTURE
        assert loaded.products[1].kind is ProductKind.FREQUENCY_MODES
        assert loaded.products[2].kind is ProductKind.ENERGY_REPORT
        assert loaded.to_dict() == manifest.to_dict()

    def test_doc_section_8_schema_keys(self, tmp_path) -> None:
        manifest = ResultManifest(task_id="task_001", workflow="optfreqsp", status="completed")
        manifest.add_product("p1", "label", "reports/x.json", ProductKind.REPORT)
        payload = json.loads(manifest.write(tmp_path).read_text(encoding="utf-8"))
        assert set(payload) == {"version", "task_id", "workflow", "status", "products"}
        assert set(payload["products"][0]) == {"id", "label", "path", "kind"}

    def test_add_product_upserts_by_id(self) -> None:
        manifest = ResultManifest()
        manifest.add_product("p", "old", "a.xyz", ProductKind.STRUCTURE)
        manifest.add_product("p", "new", "b.xyz", ProductKind.STRUCTURE)
        assert len(manifest.products) == 1
        assert manifest.products[0].path == "b.xyz"

    def test_read_missing_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            ResultManifest.read(tmp_path)

    def test_product_kind_enum_values_match_doc(self) -> None:
        assert {k.value for k in ProductKind} == {
            "structure",
            "frequency_modes",
            "energy_report",
            "ensemble",
            "trajectory",
            "report",
            "file",
            "pes_profile",
            "irc_endpoint",
            "thermo_report",
        }

    def test_unknown_kind_falls_back_to_file(self) -> None:
        product = Product.from_dict({"id": "x", "label": "y", "path": "z", "kind": "weird"})
        assert product.kind is ProductKind.FILE


class TestTaskRecord:
    def test_round_trip(self) -> None:
        record = TaskRecord(
            task_id="task_001",
            project_id="proj_001",
            molecule_name="ethanol",
            task_name="opt",
            workflow="optfreqsp",
            task_dir_name="ethanol_opt_final",
            status="completed",
            remark="final",
            display_name="ethanol opt final",
            node_id="node_a",
            node_path="/scratch/acp/ethanol_opt_final",
            input_hash="abc123",
            result_manifest_path="RESULT/result_manifest.json",
            current_stage="04_FREQ",
            created_at="2026-08-22T00:00:00Z",
            updated_at="2026-08-22T01:00:00Z",
        )
        restored = TaskRecord.from_dict(record.to_dict())
        assert restored == record
        assert restored.layout_version == 2

    def test_section_9_1_fields_present(self) -> None:
        expected = {
            "task_id",
            "project_id",
            "molecule_name",
            "task_name",
            "remark",
            "display_name",
            "workflow",
            "task_dir_name",
            "status",
            "node_id",
            "node_path",
            "input_hash",
            "result_manifest_path",
            "current_stage",
            "created_at",
            "updated_at",
            "layout_version",
        }
        record = TaskRecord(
            task_id="t",
            project_id="p",
            molecule_name="m",
            task_name="o",
            workflow="opt",
            task_dir_name="m_o",
        )
        assert expected.issubset(set(record.to_dict()))

    def test_from_dict_tolerates_extra_keys(self) -> None:
        record = TaskRecord.from_dict(
            {
                "task_id": "t",
                "project_id": "p",
                "molecule_name": "m",
                "task_name": "o",
                "workflow": "opt",
                "task_dir_name": "m_o",
                "legacy_field": "ignored",
            }
        )
        assert record.task_id == "t"
        assert record.layout_version == 2


class TestNodePathMapping:
    def test_round_trip(self) -> None:
        mapping = NodePathMapping(
            task_id="task_001",
            storage_node="node_a",
            storage_mode="sftp",
            storage_path="/scratch/acp/Concented_TS_Project/XXXTS1_mechanism_route01",
            last_seen="2026-08-22T00:00:00Z",
            input_hash="abc123",
            result_manifest_mtime=1756000000.0,
        )
        payload = mapping.to_dict()
        assert payload["result_manifest_path"] == "RESULT/result_manifest.json"
        restored = NodePathMapping.from_dict(payload)
        assert restored == mapping

    def test_section_9_3_fields_present(self) -> None:
        expected = {
            "task_id",
            "storage_node",
            "storage_mode",
            "storage_path",
            "result_manifest_path",
            "last_seen",
            "input_hash",
            "result_manifest_mtime",
        }
        mapping = NodePathMapping(task_id="t", storage_node="n", storage_path="/x")
        assert expected == set(mapping.to_dict())

    def test_frozen(self) -> None:
        mapping = NodePathMapping(task_id="t", storage_node="n", storage_path="/x")
        with pytest.raises(AttributeError):
            mapping.storage_node = "other"  # type: ignore[misc]
