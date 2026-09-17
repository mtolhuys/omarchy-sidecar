# omakit block: store 0.1.0
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Maarten Tolhuijs
# Source: omakit blocks/store/store-helper.py, commit 4188f9f12535847af263acfb25e33d162753c2c4
# Body sha256: cec3fcc9573ce7c12f1365e106cc65a936e213553bfe4e3128e8a87eea511dc5
# end of omakit block header
#
# The helper behind Store.qml, started through the Run block as
#   /usr/bin/python3 -I -S -B <this file> read|write|remove --kind state|cache
#       --plugin <id> --name <file> [--max-bytes N] [--schema JSON] [--value JSON]
# and never by hand. One private directory per plugin under the XDG base,
# reached by a descriptor walk from HOME: every directory opened with
# O_NOFOLLOW and O_DIRECTORY, checked on its descriptor to be a directory
# owned by this user and writable by nobody else, created with mode 0700
# where missing. Every file is opened relative to that descriptor with
# O_NOFOLLOW and checked the same way after the open, never before it. A
# read is capped in bytes and parsed against a schema; a write goes to an
# exclusive 0600 staging file and is renamed into place. The shape is the
# catalog cache transaction of omarchy-theme-manager 0.5.15, which the
# marketplace review read without a further file or state comment. One
# JSON line on stdout is the result; docs/BLOCKS.md is the contract.
import errno
import json
import os
import re
import secrets
import stat
import sys
import time

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
STAGING = re.compile(r"^\.store-\d+-[0-9a-f]{16}\.tmp$")
STAGING_STALE_SECONDS = 600
DEFAULT_MAX_BYTES = 1048576
KINDS = {"state": ("XDG_STATE_HOME", ".local/state"), "cache": ("XDG_CACHE_HOME", ".cache")}
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


class Refused(Exception):
    """A check on a descriptor failed: the directory or file is not ours to use."""


class Invalid(Exception):
    """The file's content is not what the schema says."""


class Overflow(Exception):
    """The file, or the value to write, is over the cap."""


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


def parse(argv):
    """The operation and its options; every value is a string, the caller converts."""
    if not argv or argv[0] not in ("read", "write", "remove"):
        raise SystemExit("store-helper: read, write or remove")
    opts = {"op": argv[0], "max_bytes": str(DEFAULT_MAX_BYTES)}
    index = 1
    while index < len(argv):
        key = argv[index][2:].replace("-", "_")
        if not argv[index].startswith("--") or key not in ("kind", "plugin", "name", "max_bytes", "schema", "value") or index + 1 >= len(argv):
            raise SystemExit("store-helper: unknown option %s" % argv[index])
        opts[key] = argv[index + 1]
        index += 2
    return opts


def check_options(opts):
    if opts.get("kind") not in KINDS:
        raise Refused("kind must be state or cache")
    for key in ("plugin", "name"):
        if not SAFE_NAME.match(opts.get(key, "")) or ".." in opts[key]:
            raise Refused("%s is not a safe name: %r" % (key, opts.get(key, "")))
    if not opts["max_bytes"].isdigit() or int(opts["max_bytes"]) < 1:
        raise Refused("max-bytes must be a positive integer")


def verify_owned(fd, label, directory):
    """The descriptor is the kind of thing expected, ours, and writable by nobody else."""
    info = os.fstat(fd)
    if directory and not stat.S_ISDIR(info.st_mode):
        raise Refused("%s is not a directory" % label)
    if not directory and not stat.S_ISREG(info.st_mode):
        raise Refused("%s is not a regular file" % label)
    if info.st_uid != os.getuid():
        raise Refused("%s is not owned by this user" % label)
    if info.st_mode & 0o022:
        raise Refused("%s is writable by the group or by others" % label)
    return info


def open_child_directory(parent_fd, name, label, create):
    """A child directory by descriptor, created 0700 when missing and allowed."""
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    fd = open_no_follow(name, DIRECTORY_FLAGS, parent_fd, label)
    try:
        verify_owned(fd, label, True)
    except Refused:
        os.close(fd)
        raise
    return fd


def open_no_follow(name, flags, dir_fd, label):
    """openat with O_NOFOLLOW; a symbolic link in the way is a refusal, by name."""
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise Refused("%s is a symbolic link" % label) from None
        if error.errno == errno.ENOTDIR and flags & os.O_DIRECTORY:
            raise Refused("%s is a symbolic link or a file, not a directory" % label) from None
        raise


def base_components(kind, environ):
    """HOME and the XDG base's path components below it; the base must be inside HOME."""
    home = environ.get("HOME", "")
    variable, default = KINDS[kind]
    base = environ.get(variable) or os.path.join(home, default)
    if not home.startswith("/") or not base.startswith("/"):
        raise Refused("HOME and %s must be absolute paths" % variable)
    home, base = os.path.normpath(home), os.path.normpath(base)
    if os.path.commonpath([home, base]) != home:
        raise Refused("%s is not inside HOME, so its ownership cannot be walked" % variable)
    relative = os.path.relpath(base, home)
    components = [] if relative == "." else relative.split(os.sep)
    if any(part in ("", ".", "..") for part in components):
        raise Refused("%s has an unsafe component" % variable)
    return home, base, components


def open_private_directory(kind, plugin, environ=None):
    """The plugin's private directory, by a descriptor walk from HOME; the descriptors on the way stay open.

    `environ` is where HOME and the XDG base are read from: the process
    environment by default, or a mapping a long-running importer passes so
    a test can point the walk at a throwaway base without touching the
    process. Measured before this: an importer's tests wrote into the
    user's real state directory.
    """
    home, base, components = base_components(kind, os.environ if environ is None else environ)
    fds = [open_no_follow(home, DIRECTORY_FLAGS, None, "HOME")]
    verify_owned(fds[0], "HOME", True)
    for part in components:
        fds.append(open_child_directory(fds[-1], part, "the %s directory" % kind, True))
    fds.append(open_child_directory(fds[-1], plugin, "the plugin's %s directory" % kind, True))
    return fds, os.path.join(base, plugin)


def read_capped(fd, maximum):
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, min(65536, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise Overflow("the file is over %d bytes" % maximum)


def schema_problem(value, schema, path):
    """The first way `value` departs from the schema subset, or None."""
    expected = schema.get("type")
    if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return "%s is not a number" % path
    if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return "%s is not an integer" % path
    if expected in TYPES and not isinstance(value, TYPES[expected]):
        return "%s is not %s" % (path, expected)
    if "enum" in schema and value not in schema["enum"]:
        return "%s is not one of the allowed values" % path
    return bounds_problem(value, schema, path) or members_problem(value, schema, path)


def bounds_problem(value, schema, path):
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        return "%s is longer than %d" % (path, schema["maxLength"])
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        return "%s does not match the pattern" % path
    if isinstance(value, list) and len(value) > schema.get("maxItems", len(value)):
        return "%s has more than %d items" % (path, schema["maxItems"])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            return "%s is out of range" % path
    if isinstance(value, dict) and len(value) > schema.get("maxProperties", len(value)):
        return "%s has more than %d keys" % (path, schema["maxProperties"])
    return None


def members_problem(value, schema, path):
    if isinstance(value, dict):
        return object_problem(value, schema, path)
    if isinstance(value, list) and "items" in schema:
        return array_problem(value, schema, path)
    return None


def object_problem(value, schema, path):
    for key in schema.get("required", []):
        if key not in value:
            return "%s lacks %s" % (path, key)
    for key, member in value.items():
        member_schema = schema.get("properties", {}).get(key)
        if member_schema is None and schema.get("additionalProperties") is False:
            return "%s has an unexpected key %s" % (path, key)
        problem = schema_problem(member, member_schema, "%s.%s" % (path, key)) if member_schema else None
        if problem:
            return problem
    return None


def array_problem(value, schema, path):
    for index, item in enumerate(value):
        problem = schema_problem(item, schema["items"], "%s[%d]" % (path, index))
        if problem:
            return problem
    return None


def parse_value(data, schema):
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, ValueError) as error:
        raise Invalid("not valid JSON: %s" % error) from None
    problem = schema_problem(value, schema, "value") if schema else None
    if problem:
        raise Invalid(problem)
    return value


def read_file(dir_fd, name, maximum, schema):
    try:
        fd = open_no_follow(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd, name)
    except FileNotFoundError:
        return {"state": "missing"}
    try:
        info = verify_owned(fd, name, False)
        data = read_capped(fd, maximum)
    finally:
        os.close(fd)
    return {"state": "ok", "value": parse_value(data, schema), "bytes": len(data), "mtime": int(info.st_mtime)}


def stale_staging(dir_fd, entry):
    """A staging file of ours, older than a crashed writer could still be using."""
    if not STAGING.match(entry):
        return False
    try:
        info = os.stat(entry, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and time.time() - info.st_mtime > STAGING_STALE_SECONDS


def unlink_quietly(dir_fd, entry):
    try:
        os.unlink(entry, dir_fd=dir_fd)
    except OSError:
        pass


def sweep_staging(dir_fd):
    """Staging files a crashed writer left behind, once they are old enough to be nobody's."""
    for entry in os.listdir(dir_fd):
        if stale_staging(dir_fd, entry):
            unlink_quietly(dir_fd, entry)


def create_staging(dir_fd):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _attempt in range(100):
        name = ".store-%d-%s.tmp" % (os.getpid(), secrets.token_hex(8))
        try:
            return name, os.open(name, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
    raise Refused("could not create an exclusive staging file")


def write_file(dir_fd, name, maximum, schema, raw):
    data = json.dumps(parse_value(raw.encode("utf-8"), schema), ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    if len(data) > maximum:
        raise Overflow("the value is %d bytes, over %d" % (len(data), maximum))
    sweep_staging(dir_fd)
    staging, fd = create_staging(dir_fd)
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.rename(staging, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        staging = None
        os.fsync(dir_fd)
    finally:
        os.close(fd)
        if staging:
            os.unlink(staging, dir_fd=dir_fd)
    return {"state": "ok", "bytes": len(data)}


def remove_file(dir_fd, name):
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return {"state": "missing"}
    os.fsync(dir_fd)
    return {"state": "ok"}


def operate(opts, environ=None):
    check_options(opts)
    schema = json.loads(opts["schema"]) if opts.get("schema") else None
    fds, path = open_private_directory(opts["kind"], opts["plugin"], environ)
    try:
        if opts["op"] == "read":
            result = read_file(fds[-1], opts["name"], int(opts["max_bytes"]), schema)
        elif opts["op"] == "write":
            result = write_file(fds[-1], opts["name"], int(opts["max_bytes"]), schema, opts.get("value", ""))
        else:
            result = remove_file(fds[-1], opts["name"])
    finally:
        for fd in fds:
            os.close(fd)
    result["path"] = os.path.join(path, opts["name"])
    return result


def result_of(opts, environ=None):
    """One operation as the result object Store.qml reports: importable by a long-running program that keeps its own state through this file."""
    try:
        result = operate(opts, environ)
    except Refused as why:
        result = {"state": "refused", "reason": str(why)}
    except Invalid as why:
        result = {"state": "invalid", "reason": str(why)}
    except Overflow as why:
        result = {"state": "overflow", "reason": str(why)}
    except (OSError, ValueError) as why:
        result = {"state": "failed", "reason": "%s: %s" % (type(why).__name__, why)}
    return dict(result, op=opts["op"])


def main():
    opts = parse(sys.argv[1:])
    emit(dict(result_of(opts), ev="result"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
