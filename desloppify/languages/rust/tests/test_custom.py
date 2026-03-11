"""Tests for Rust-specific policy detectors and fixers."""

from __future__ import annotations

from pathlib import Path

from desloppify.base.runtime_state import RuntimeContext, runtime_scope
from desloppify.languages import get_lang
from desloppify.languages.rust._fixers import (
    fix_crate_imports,
    fix_missing_features,
    fix_readme_doctests,
)
from desloppify.languages.rust.phases import phase_signature
from desloppify.languages.rust.detectors.custom import (
    detect_doctest_hygiene,
    detect_error_boundaries,
    detect_feature_hygiene,
    detect_future_proofing,
    detect_import_hygiene,
    detect_public_api_conventions,
    detect_thread_safety_contracts,
)


def _write(path: Path, rel_path: str, content: str) -> Path:
    target = path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_detect_import_hygiene_and_fix_rewrites_same_crate_paths(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    source = _write(
        tmp_path,
        "src/lib.rs",
        "use demo_app::support::Thing;\npub fn run() {}\n",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_import_hygiene(tmp_path)
        assert [entry["name"] for entry in entries] == ["crate_import::1"]
        result = fix_crate_imports(entries, dry_run=False)

    assert result.entries[0]["file"] == "src/lib.rs"
    assert source.read_text() == "use crate::support::Thing;\npub fn run() {}\n"


def test_detect_import_hygiene_ignores_doctest_imports(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        '/// ```rust\n/// use demo_app::support::Thing;\n/// ```\npub fn run() {}\n',
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_import_hygiene(tmp_path)

    assert entries == []


def test_detect_feature_hygiene_and_fix_adds_missing_features(tmp_path):
    manifest = _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        '#[cfg(feature = "experimental")]\npub fn experiment() {}\n',
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_feature_hygiene(tmp_path)
        assert [entry["name"] for entry in entries] == ["experimental"]
        result = fix_missing_features(entries, dry_run=False)

    assert result.entries[0]["file"] == "Cargo.toml"
    assert "[features]\nexperimental = []\n" in manifest.read_text()


def test_detect_feature_hygiene_ignores_optional_dependency_features(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        """
[package]
name = "demo-app"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", optional = true }
""",
    )
    _write(
        tmp_path,
        "src/lib.rs",
        '#[cfg(feature = "serde")]\npub fn encode() {}\n',
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_feature_hygiene(tmp_path)

    assert entries == []


def test_detect_doctest_hygiene_and_fix_adds_readme_harness(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    lib_rs = _write(tmp_path, "src/lib.rs", "//! Demo crate.\npub fn run() {}\n")
    _write(tmp_path, "README.md", "```rust\nuse demo_app::run;\n```\n")

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_doctest_hygiene(tmp_path)
        assert [entry["name"] for entry in entries] == ["readme_doctests"]
        result = fix_readme_doctests(entries, dry_run=False)

    assert result.entries[0]["file"] == "src/lib.rs"
    assert "include_str!(\"../README.md\")" in lib_rs.read_text()


def test_detect_doctest_hygiene_skips_when_lib_already_has_examples(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        '//! ```rust\n//! use demo_app::run;\n//! ```\npub fn run() {}\n',
    )
    _write(tmp_path, "README.md", "```rust\nuse demo_app::run;\n```\n")

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_doctest_hygiene(tmp_path)

    assert entries == []


def test_detect_public_api_convention_flags_getter_and_into_borrow(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
pub struct User;

impl User {
    pub fn get_name(&self) -> &str { "name" }
    pub fn into_name(&self) -> String { "name".to_string() }
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_public_api_conventions(tmp_path)

    assert {entry["name"] for entry in entries} == {"getter::get_name", "into_ref::into_name"}


def test_detect_public_api_convention_ignores_lookup_methods_and_pyo3_getters(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
pub struct User;

impl User {
    #[getter]
    pub fn get_name(&self) -> &str { "name" }

    pub fn get_template(&self, name: &str) -> Option<&str> { Some(name) }
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_public_api_conventions(tmp_path)

    assert entries == []


def test_detect_error_boundaries_flags_anyhow_and_panic_paths(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
pub fn parse() -> anyhow::Result<()> {
    panic!("nope");
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_error_boundaries(tmp_path)

    assert {entry["name"] for entry in entries} == {"error_type::parse", "panic_path::parse"}


def test_detect_error_boundaries_ignores_infallible_write_unwrap(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
use std::fmt::Write;

pub fn format_name() -> String {
    let mut value = String::new();
    write!(&mut value, "demo").unwrap();
    value
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_error_boundaries(tmp_path)

    assert entries == []


def test_detect_future_proofing_flags_public_struct_shape(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
pub struct Config {
    pub host: String,
    pub port: u16,
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_future_proofing(tmp_path)

    assert [entry["name"] for entry in entries] == ["struct::Config"]


def test_detect_future_proofing_skips_ffi_and_unstable_docs(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
/// This is unstable machinery.
#[repr(C)]
pub struct ApiConfig {
    pub host: String,
    pub port: u16,
    pub tls: bool,
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_future_proofing(tmp_path)

    assert entries == []


def test_detect_thread_safety_contracts_flags_manual_send_without_assertions(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
use std::cell::RefCell;

pub struct SharedState {
    inner: RefCell<String>,
}

unsafe impl Send for SharedState {}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_thread_safety_contracts(tmp_path)

    assert [entry["name"] for entry in entries] == ["thread_contract::SharedState"]


def test_detect_thread_safety_contracts_ignores_repr_c_ffi_structs(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    _write(
        tmp_path,
        "src/lib.rs",
        """
#[repr(C)]
pub struct SharedState {
    pub raw: *mut u8,
}
""",
    )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        entries, _ = detect_thread_safety_contracts(tmp_path)

    assert entries == []


def test_phase_signature_skips_common_rust_constructors(tmp_path):
    _write(
        tmp_path,
        "Cargo.toml",
        '[package]\nname = "demo-app"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    for index in range(4):
        _write(
            tmp_path,
            f"src/module_{index}.rs",
            f"""
pub struct Service{index};

impl Service{index} {{
    pub fn new(value: i32) -> Self {{
        Self
    }}
}}
""",
        )

    with runtime_scope(RuntimeContext(project_root=tmp_path)):
        issues, potentials = phase_signature(tmp_path, get_lang("rust"))

    assert issues == []
    assert potentials == {}
