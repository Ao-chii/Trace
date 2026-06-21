from app.services.mutation_discovery import discover_mutation_candidates


def test_mutation_discovery_dry_run_finds_boundary_candidates_and_samples_deterministically(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def shipping_fee(weight_kg):\n"
        "    if weight_kg <= 5:\n"
        "        return 10\n"
        "    if weight_kg >= 10:\n"
        "        return 30\n"
        "    return 20\n\n"
        "def untouched(value):\n"
        "    return value == 1\n",
        encoding="utf-8",
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pricing.py").write_text(
        "def helper_test_target(value):\n"
        "    return value <= 5\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-pricing",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["shop.pricing.shipping_fee"]},
        sample_seed=7,
        max_selected=1,
    )
    repeat = discover_mutation_candidates(
        root=project,
        eval_task_id="task-pricing",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["shop.pricing.shipping_fee"]},
        sample_seed=7,
        max_selected=1,
    )

    assert result == repeat
    assert result.selected_count == 1
    assert result.excluded_count == 0
    assert len(result.candidates) == 2
    assert {candidate.patch.old for candidate in result.candidates} == {"weight_kg <= 5", "weight_kg >= 10"}
    assert {candidate.patch.new for candidate in result.candidates} == {"weight_kg < 5", "weight_kg > 10"}
    assert all(candidate.patch.file == "shop/pricing.py" for candidate in result.candidates)
    assert all(candidate.matcher.target_symbol == "shipping_fee" for candidate in result.candidates)
    selected = [candidate for candidate in result.candidates if candidate.selection.status == "selected"]
    assert selected[0].selection.sample_seed == 7
    assert selected[0].selection.sample_index == 0
    assert not any(candidate.patch.file.startswith("tests/") for candidate in result.candidates)


def test_mutation_discovery_dry_run_finds_arithmetic_candidates(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "totals.py").write_text(
        "def checkout_total(price, shipping):\n"
        "    return price + shipping\n\n"
        "def apply_discount(total, discount):\n"
        "    return total - discount\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-totals",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["checkout_total", "apply_discount"]},
        sample_seed=2,
        max_selected=10,
    )

    assert result.selected_count == 2
    assert result.excluded_count == 0
    assert {candidate.operator for candidate in result.candidates} == {"arithmetic_operator"}
    assert {candidate.patch.old for candidate in result.candidates} == {"price + shipping", "total - discount"}
    assert {candidate.patch.new for candidate in result.candidates} == {"price - shipping", "total + discount"}
    assert all(candidate.matcher.operator == candidate.operator for candidate in result.candidates)
    assert all(candidate.selection.status == "selected" for candidate in result.candidates)


def test_mutation_discovery_dry_run_finds_boolean_negation_candidates(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "flags.py").write_text(
        "def is_enabled(flag):\n"
        "    return flag\n\n"
        "def already_negated(flag):\n"
        "    return not flag\n\n"
        "def identity(value):\n"
        "    return value\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-flags",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["is_enabled", "already_negated", "identity"]},
        sample_seed=5,
        max_selected=10,
    )

    assert result.selected_count == 1
    assert result.excluded_count == 0
    [candidate] = result.candidates
    assert candidate.operator == "boolean_negation"
    assert candidate.patch.file == "shop/flags.py"
    assert candidate.patch.old == "return flag"
    assert candidate.patch.new == "return not flag"
    assert candidate.matcher.operator == candidate.operator
    assert candidate.matcher.target_symbol == "is_enabled"
    assert candidate.selection.status == "selected"


def test_mutation_discovery_dry_run_finds_boolean_constant_replacement_candidates(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "availability.py").write_text(
        "def is_available():\n"
        "    return True\n\n"
        "def is_disabled():\n"
        "    return False\n\n"
        "def count():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-availability",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["is_available", "is_disabled", "count"]},
        sample_seed=6,
        max_selected=10,
    )

    assert result.selected_count == 2
    assert result.excluded_count == 0
    assert {candidate.operator for candidate in result.candidates} == {"constant_replacement"}
    assert {candidate.patch.old for candidate in result.candidates} == {"return True", "return False"}
    assert {candidate.patch.new for candidate in result.candidates} == {"return False", "return True"}
    assert {candidate.matcher.target_symbol for candidate in result.candidates} == {"is_available", "is_disabled"}
    assert all(candidate.matcher.operator == candidate.operator for candidate in result.candidates)
    assert all(candidate.selection.status == "selected" for candidate in result.candidates)


def test_mutation_discovery_dry_run_finds_none_return_value_candidates(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "lookup.py").write_text(
        "def find_user(user_id):\n"
        "    return None\n\n"
        "def is_available():\n"
        "    return True\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-lookup",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["find_user", "is_available"]},
        sample_seed=8,
        max_selected=10,
    )

    assert result.selected_count == 2
    assert result.excluded_count == 0
    by_operator = {candidate.operator: candidate for candidate in result.candidates}
    assert by_operator["return_value"].patch.old == "return None"
    assert by_operator["return_value"].patch.new == "return False"
    assert by_operator["return_value"].matcher.target_symbol == "find_user"
    assert by_operator["constant_replacement"].patch.old == "return True"
    assert all(candidate.selection.status == "selected" for candidate in result.candidates)

def test_mutation_discovery_dry_run_reports_exclusions_without_writing_variants(tmp_path):
    project = tmp_path / "proj"
    package = project / "shop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pricing.py").write_text(
        "def repeated(value):\n"
        "    if value <= 5:\n"
        "        return 1\n"
        "    if value <= 5:\n"
        "        return 2\n"
        "    return 3\n\n"
        "def chained(value, upper):\n"
        "    return 1 < value < upper\n\n"
        "def equality(value):\n"
        "    if value == 1:\n"
        "        return 1\n"
        "    return value != 2\n",
        encoding="utf-8",
    )

    result = discover_mutation_candidates(
        root=project,
        eval_task_id="task-pricing",
        source_snapshot_id="snap-clean",
        target_scope={"targets": ["repeated", "chained", "equality", "missing_target"]},
        sample_seed=0,
        max_selected=10,
    )

    assert len(result.candidates) == 2
    assert {candidate.operator for candidate in result.candidates} == {"comparison_negation"}
    assert {candidate.patch.old for candidate in result.candidates} == {"value == 1", "value != 2"}
    assert {candidate.patch.new for candidate in result.candidates} == {"value != 1", "value == 2"}
    assert all(candidate.matcher.operator == candidate.operator for candidate in result.candidates)
    assert all(candidate.selection.status == "selected" for candidate in result.candidates)
    reason_codes = [exclusion.reason_code for exclusion in result.exclusions]
    assert "non_unique_patch" in reason_codes
    assert reason_codes.count("unsupported_compare") == 1
    assert "target_not_found" in reason_codes
    assert result.excluded_count == len(result.exclusions)
    missing = [exclusion for exclusion in result.exclusions if exclusion.reason_code == "target_not_found"]
    assert missing[0].target_ref == "missing_target"
