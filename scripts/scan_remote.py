#!/usr/bin/env python3
"""scripts/scan_remote.py — clone a git repo into a temp dir, run
``lacuna check`` against it, and clean up.

Use as a smoke test that lacuna's tooling works on real-world
codebases, especially after adding a new language extractor. Runs
shallow clones by default (`git clone --depth 1`) so even very large
repos use modest disk space.

Usage:
    python scripts/scan_remote.py URL [options]
    python scripts/scan_remote.py --language LANG [options]
    python scripts/scan_remote.py --list

URL mode clones whatever you point at. Language mode picks a
sanity-check corpus from KNOWN_CORPORA below — interactively if
multiple are listed, automatically if there's only one.

Options:
    --keep                Leave the clone in place after scanning
    --full                Full clone instead of shallow (--depth 1)
    --languages X[,Y,..]  Restrict lacuna to these languages
                          (writes a temp lacuna.toml in the clone)
    --min-confidence N    Pass through to lacuna check
    --json                Pass through to lacuna check

═══════════════════════════════════════════════════════════════════════
CONVENTION: when adding a new language extractor, add at least one
entry to KNOWN_CORPORA below. Pick a public repo that's:

  - Idiomatic for the language
  - Small-to-medium sized (clones quickly with --depth 1)
  - Convention-rich (so lacuna actually finds something)
  - Well-maintained (won't disappear)

This list is *the* sanity-check resource. If a language ships in
lacuna without a corpus entry here, we have no quick way to verify
the extractor still works on real code.
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# Curated list of public repos for sanity-checking each extractor.
# Each entry: (url, short-description). First entry per language is the
# default when --language is given non-interactively.
KNOWN_CORPORA: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("https://github.com/pallets/flask",
         "Decorator-heavy web framework — best signal for @route patterns"),
        ("https://github.com/psf/requests",
         "Convention-rich HTTP library, idiomatic Python"),
    ],
    "javascript": [
        ("https://github.com/expressjs/express",
         "Widely conventional, ~6k LOC of hand-written JS"),
        ("https://github.com/sindresorhus/got",
         "Modern JS HTTP library, class-based"),
    ],
    "typescript": [
        ("https://github.com/nestjs/nest",
         "Heavy decorator usage (Angular-style) — best TS smoke test"),
        ("https://github.com/typeorm/typeorm",
         "Decorator + interface heavy ORM"),
    ],
    "rust": [
        ("https://github.com/BurntSushi/ripgrep",
         "Clean idiomatic Rust, ~30k LOC"),
        ("https://github.com/clap-rs/clap",
         "Trait-heavy with many derive macros"),
    ],
    "go": [
        ("https://github.com/urfave/cli",
         "Compact, idiomatic CLI library"),
        ("https://github.com/spf13/cobra",
         "Method-pattern heavy"),
    ],
    "java": [
        ("https://github.com/google/gson",
         "Idiomatic JSON library, lots of class hierarchy"),
        ("https://github.com/junit-team/junit5",
         "Annotation-heavy testing framework — best signal for @Test patterns"),
    ],
    "swift": [
        ("https://github.com/Alamofire/Alamofire",
         "Real Swift idioms, structs + protocols"),
        ("https://github.com/realm/SwiftLint",
         "Lots of types + extensions"),
    ],
}


def _print_known(languages: list[str] | None = None) -> None:
    """Print the corpora list, optionally filtered to specific languages."""
    keys = languages or sorted(KNOWN_CORPORA)
    for lang in keys:
        if lang not in KNOWN_CORPORA:
            print(f"({lang}: no corpora registered)")
            continue
        print(f"{lang}:")
        for i, (url, note) in enumerate(KNOWN_CORPORA[lang], 1):
            print(f"  {i}. {url}")
            print(f"     {note}")
        print()


def _pick_corpus(language: str) -> str | None:
    """Return a corpus URL for the named language. Prompts interactively
    if multiple options exist; auto-picks if only one. Returns None if
    no corpora are registered for the language."""
    options = KNOWN_CORPORA.get(language)
    if not options:
        return None
    if len(options) == 1:
        url, note = options[0]
        print(f"Using {url}")
        print(f"  ({note})")
        return url

    print(f"{language} corpora:")
    for i, (url, note) in enumerate(options, 1):
        print(f"  {i}. {url}")
        print(f"     {note}")
    print()
    if not sys.stdin.isatty():
        # Non-interactive: auto-pick first.
        url = options[0][0]
        print(f"(non-TTY input — auto-selecting first option)")
        print(f"Using {url}")
        return url

    try:
        choice = input(f"Pick one [1-{len(options)}, default 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not choice:
        choice = "1"
    try:
        idx = int(choice)
    except ValueError:
        print(f"Invalid choice: {choice!r}")
        return None
    if not 1 <= idx <= len(options):
        print(f"Out of range: {idx}")
        return None
    return options[idx - 1][0]


def _clone(url: str, dest: Path, *, shallow: bool) -> int:
    cmd = ["git", "clone"]
    if shallow:
        cmd += ["--depth", "1"]
    cmd += [url, str(dest)]
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def _write_languages_config(dest: Path, languages: list[str]) -> None:
    """Drop a minimal lacuna.toml restricting scan.languages."""
    lines = ["[scan]\nlanguages = ["]
    for lang in languages:
        lines.append(f'    "{lang}",')
    lines.append("]\n")
    (dest / "lacuna.toml").write_text("\n".join(lines))


def _run_lacuna(
    dest: Path, *,
    min_confidence: float | None, as_json: bool,
) -> int:
    cmd = [
        shutil.which("lacuna") or "lacuna",
        "check", str(dest),
    ]
    if min_confidence is not None:
        cmd += ["--min-confidence", str(min_confidence)]
    if as_json:
        cmd += ["--json"]
    print(f"\n$ {' '.join(cmd)}\n")
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_remote",
        description="Clone a git repo, scan it with lacuna, clean up.",
    )
    src_group = parser.add_mutually_exclusive_group()
    src_group.add_argument("url", nargs="?",
                          help="Git URL to clone and scan")
    src_group.add_argument("--language", help="Pick a known corpus for "
                          "this language from KNOWN_CORPORA")
    parser.add_argument("--list", action="store_true", dest="list_known",
                       help="List the known sanity-check corpora and exit")
    parser.add_argument("--keep", action="store_true",
                       help="Leave the clone in place after scanning")
    parser.add_argument("--full", action="store_true",
                       help="Full clone instead of shallow --depth 1")
    parser.add_argument("--languages", default=None,
                       help="Comma-separated list of languages to restrict "
                            "lacuna's scan to (writes a temp lacuna.toml)")
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.list_known:
        _print_known()
        return 0

    if args.url:
        url = args.url
    elif args.language:
        url = _pick_corpus(args.language)
        if url is None:
            return 1
    else:
        parser.print_help()
        return 1

    tmp_root = Path(tempfile.mkdtemp(prefix="lacuna_scan_"))
    try:
        clone_started = time.perf_counter()
        rc = _clone(url, tmp_root, shallow=not args.full)
        if rc != 0:
            print(f"Clone failed (exit {rc})", file=sys.stderr)
            return rc
        clone_seconds = time.perf_counter() - clone_started
        print(f"\nCloned in {clone_seconds:.1f}s")

        if args.languages:
            languages = [s.strip() for s in args.languages.split(",")
                         if s.strip()]
            _write_languages_config(tmp_root, languages)

        return _run_lacuna(
            tmp_root,
            min_confidence=args.min_confidence,
            as_json=args.as_json,
        )
    finally:
        if args.keep:
            print(f"\n--keep set; clone remains at {tmp_root}")
        else:
            print(f"\nRemoving {tmp_root}...")
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
