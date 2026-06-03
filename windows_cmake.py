#!/usr/bin/env python3
"""
Generic Windows CMake helper script.

Handles discovery of CMake, Ninja, and MSVC (via vswhere) so you don't need
to launch from a Visual Studio Developer Command Prompt.

Usage:
    python windows_cmake.py -S <src> -B <build>
    python windows_cmake.py -S <src> -B <build> --config
    python windows_cmake.py -S <src> -B <build> --build
    python windows_cmake.py -S <src> -B <build> --clean
    python windows_cmake.py -S <src> -B <build> --verify libfoo.dll
    python windows_cmake.py -S <src> -B <build> --type Debug
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_BUILD_TYPE = "Release"
DEFAULT_GENERATOR = "Ninja"


def run(cmd: list[str], cwd=None, timeout: int = 300, env=None,
        check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, streaming output. Exits on failure if check=True."""
    print(f"[RUN] {' '.join(str(c) for c in cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                          env=env or os.environ, capture_output=False)
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc


def run_capture(cmd: list[str], cwd=None, timeout: int = 30) -> str:
    """Run a command, capture stdout, raise on failure."""
    proc = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)
    return proc.stdout.strip()


def find_program(name: str) -> str | None:
    """Find a program in PATH, .deps, or common locations."""
    # 1. PATH
    path = shutil.which(name)
    if path:
        print(f"[FOUND] {name}: {path}")
        return path

    # 2. Bootstrap .deps directory
    script_dir = Path(__file__).resolve().parent
    deps = script_dir / ".deps"
    if deps.is_dir():
        candidate = deps / f"{name}.exe" if sys.platform == "win32" else deps / name
        if candidate.is_file():
            print(f"[FOUND] {name}: {candidate}")
            return str(candidate)

    # 3. Python scripts dir (for ninja installed via pip)
    if name == "ninja":
        pip_ninja = Path(sys.executable).parent / "ninja.exe"
        if pip_ninja.is_file():
            print(f"[FOUND] {name}: {pip_ninja}")
            return str(pip_ninja)

    print(f"[MISSING] {name}")
    return None


def prepare_msvc_environment() -> dict:
    """Detect and activate MSVC environment using vswhere."""

    def find_msvc(pattern: str) -> list[str]:
        vswhere = find_program("vswhere.exe")
        if not vswhere:
            return []

        result = run_capture([
            vswhere, "-format", "json", "-utf8", "-nologo", "-sort",
            "-products", "*", "-find", pattern, "-latest",
        ])
        data = json.loads(result)
        return [x.replace("\\", "/") for x in data]

    vcvars = find_msvc("**/Auxiliary/Build/vcvars64.bat")
    if not vcvars:
        print("[WARN] Could not find vcvars64.bat. Proceeding without MSVC environment.")
        return os.environ.copy()

    vcvars_bat = vcvars[0]
    print(f"[MSVC] Using: {vcvars_bat}")

    dump_cmd = (
        f'"{vcvars_bat}" && python -c '
        f'"import os, json; print(json.dumps(dict(os.environ)))"'
    )
    result = subprocess.run(
        dump_cmd, shell=True, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"[WARN] vcvars failed: {result.stderr}")
        return os.environ.copy()

    env_vars = json.loads(result.stdout.strip())
    env = os.environ.copy()
    env.update(env_vars)
    print("[MSVC] Environment prepared.")
    return env


def configure(source_dir: Path, build_dir: Path, env: dict,
              build_type: str = DEFAULT_BUILD_TYPE,
              generator: str = DEFAULT_GENERATOR,
              extra_flags: list[str] | None = None):
    """Run CMake configure."""
    build_dir.mkdir(parents=True, exist_ok=True)

    cmake = find_program("cmake")
    if not cmake:
        sys.exit("ERROR: cmake not found. Install it or run from a VS Developer Command Prompt.")

    ninja = find_program("ninja")

    cmd = [
        cmake, "-S", str(source_dir), "-B", str(build_dir),
        "-G", generator,
        f"-DCMAKE_BUILD_TYPE={build_type}",
    ]
    if ninja:
        cmd += [f"-DCMAKE_MAKE_PROGRAM={ninja}"]
    if extra_flags:
        cmd += extra_flags

    run(cmd, env=env)


def build(build_dir: Path, env: dict, jobs: int | None = None):
    """Run CMake build."""
    if jobs is None:
        jobs = os.cpu_count() or 8

    cmake = find_program("cmake")
    if not cmake:
        sys.exit("ERROR: cmake not found.")

    run([cmake, "--build", str(build_dir), "-j", str(jobs)], env=env)


def clean(build_dir: Path):
    """Remove CMake cache to force re-configure."""
    cache = build_dir / "CMakeCache.txt"
    if cache.is_file():
        print(f"[CLEAN] Removing {cache}")
        cache.unlink()
    cmake_files = build_dir / "CMakeFiles"
    if cmake_files.is_dir():
        print(f"[CLEAN] Removing {cmake_files}")
        shutil.rmtree(cmake_files, ignore_errors=True)


def verify(build_dir: Path, expected_files: list[str]):
    """Verify expected build output files exist."""
    print("\n[VERIFY] Checking build outputs...")
    all_good = True
    for f in expected_files:
        path = build_dir / f
        if path.is_file():
            size_kb = path.stat().st_size // 1024
            print(f"  [OK] {path.name}  ({size_kb} KB)")
        else:
            print(f"  [MISSING] {path.name}")
            all_good = False

    if all_good:
        print("[VERIFY] All expected outputs present.\n")
    else:
        print("[VERIFY] Some outputs missing! Build may have failed.\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Generic Windows CMake helper")
    parser.add_argument("-S", "--source", required=True, type=Path,
                        help="Source directory containing CMakeLists.txt")
    parser.add_argument("-B", "--build", required=True, type=Path,
                        help="Build directory")
    parser.add_argument("--config", action="store_true",
                        help="Run CMake configure")
    parser.add_argument("--build-step", action="store_true",
                        help="Run CMake build")
    parser.add_argument("--clean", action="store_true",
                        help="Clean build cache before configure")
    parser.add_argument("--verify", nargs="*", default=None,
                        help="Verify expected output files (relative to build dir)")
    parser.add_argument("--type", default=DEFAULT_BUILD_TYPE,
                        choices=["Release", "Debug"],
                        help="Build type (default: Release)")
    parser.add_argument("-G", "--generator", default=DEFAULT_GENERATOR,
                        help="CMake generator (default: Ninja)")
    parser.add_argument("-D", "--define", action="append", default=[],
                        help="Extra CMake definitions, e.g. -DFOO=BAR")
    parser.add_argument("-j", type=int, default=None,
                        help="Parallel jobs (default: cpu_count)")
    args = parser.parse_args()

    # Default: config + build if no flags given
    do_config = args.config or args.build_step or args.verify is not None
    do_build = args.build_step or args.verify is not None
    do_verify = args.verify is not None
    if not any([args.config, args.build_step, args.clean, args.verify is not None]):
        do_config = True
        do_build = True

    env = prepare_msvc_environment()

    if args.clean:
        clean(args.build)

    if do_config:
        configure(
            source_dir=args.source,
            build_dir=args.build,
            env=env,
            build_type=args.type,
            generator=args.generator,
            extra_flags=[f"-D{d}" for d in args.define],
        )

    if do_build:
        build(args.build, env=env, jobs=args.j)

    if do_verify:
        verify(args.build, args.verify)

    print("[DONE] All steps completed successfully.")


if __name__ == "__main__":
    main()
