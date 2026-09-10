"""Native postprocessing with section-preserving, staged file output."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path as FilePath
import re
import shlex
import tempfile
from typing import TYPE_CHECKING

import Path
from Path.Post.Processor import PostProcessorFactory, WrapperPost, _TempObject
import Path.Base.Util as PathUtil
from Path.Post.Utils import FilenameGenerator

if TYPE_CHECKING:
    from .cam_operations import operation_job
    from .cam_jobs import job_validation


@contextmanager
def temporary_post_settings(job, args, output_file):
    original = job.PostProcessorArgs, job.PostProcessorOutputFile
    try:
        job.PostProcessorArgs = args
        job.PostProcessorOutputFile = output_file
        yield
    finally:
        job.PostProcessorArgs, job.PostProcessorOutputFile = original


def postprocessor_for(job, name):
    name = name or job.PostProcessor or Path.Preferences.defaultPostProcessor()
    if not name or name not in Path.Preferences.allAvailablePostProcessors():
        raise ValueError("Installed postprocessor not found: " + str(name))
    processor = PostProcessorFactory.get_post_processor(job, name)
    if processor is None:
        raise ValueError("Unable to load postprocessor: " + name)
    return name, processor


def postprocessor_info(job_name, name):
    job = operation_job(job_name)
    name, post = postprocessor_for(job, name)
    help_text = post.tooltipArgs
    if isinstance(help_text, (list, tuple)):
        help_text = "\n".join(str(part) for part in help_text)
    parser = getattr(post, "parser", None)
    if parser is None and isinstance(post, WrapperPost):
        parser = getattr(post.script_module, "parser", None)
    actions = getattr(parser, "_actions", [])
    return {
        "post_processor": name,
        "implementation": type(post).__name__,
        "legacy_wrapper": isinstance(post, WrapperPost),
        "units": post.units,
        "description": str(post.tooltip),
        "argument_help": str(help_text),
        "arguments": [
            {
                "options": list(action.option_strings),
                "required": action.required,
                "default": str(action.default),
                "choices": list(action.choices) if action.choices is not None else None,
            }
            for action in actions
        ],
        "job_args": job.PostProcessorArgs,
    }


def noninteractive_post_args(post, argument_string):
    tokens = shlex.split(argument_string)
    if any(token in ("-h", "--help", "--output_all_arguments") for token in tokens):
        raise ValueError(
            "Use cam_get_post_processor_info for help instead of exporting help text as G-code"
        )
    parser = getattr(post, "parser", None)
    if parser is None and isinstance(post, WrapperPost):
        parser = getattr(post.script_module, "parser", None)
    options = set(getattr(parser, "_option_string_actions", {}))
    help_text = str(post.tooltipArgs)
    if "--no-show-editor" in options or "--no-show-editor" in help_text:
        tokens = [
            token
            for token in tokens
            if token not in ("--show-editor", "--no-show-editor")
        ]
        tokens.append("--no-show-editor")
    return shlex.join(tokens)


@contextmanager
def complete_split_initialization(post, job):
    """Fix native Fixture carry-over state on this processor instance only.

    The installed Fixture builder retains the previous section's controller
    and adds a clearance rapid before the first tool change. Split sections
    are independent programs. Preserve native section ordering and let the
    postprocessor encode controllers/WCS; never splice G-code strings.
    """
    build = post._buildPostList
    previous = post.__dict__.get("_buildPostList")

    def build_sections():
        sections = []
        for label, objects in build():
            filtered = [
                obj
                for obj in objects
                if obj not in job.Operations.Group or PathUtil.activeForOp(obj)
            ]
            if job.SplitOutput:
                initialized = False
                normalized = []
                for obj in filtered:
                    if obj in job.Tools.Group:
                        initialized = True
                    elif isinstance(obj, _TempObject) and not initialized:
                        # Match native __fixtureSetup(order=0) at each file start:
                        # WCS selection remains, the inter-fixture rapid does not.
                        fixture = _TempObject()
                        fixture.InList = [job]
                        fixture.Path = Path.Path(
                            [
                                c
                                for c in obj.Path.Commands
                                if c.Name not in ("G0", "G00")
                            ]
                        )
                        obj = fixture
                    elif obj in job.Operations.Group and not initialized:
                        controller = PathUtil.toolControllerForOp(obj)
                        if controller is not None:
                            normalized.append(controller)
                            initialized = True
                    normalized.append(obj)
                filtered = normalized
            sections.append((label, filtered))
        return sections

    post._buildPostList = build_sections
    try:
        yield
    finally:
        if previous is None:
            del post._buildPostList
        else:
            post._buildPostList = previous


def write_output_files(outputs, overwrite):
    """Stage every file before replacing any destination; restore on failure."""
    paths = [path for path, _ in outputs]
    if len(paths) != len(set(paths)):
        raise ValueError("Output sections resolve to duplicate file paths")
    for path in paths:
        if not path.parent.is_dir():
            raise FileNotFoundError(
                "Output directory does not exist: " + str(path.parent)
            )
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Output must be a regular file: " + str(path))
        if path.exists() and not overwrite:
            raise FileExistsError("Output already exists: " + str(path))
    staged, backups, written = [], {}, []
    try:
        for path, data in outputs:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".mcp-post-", delete=False
            ) as stream:
                staged.append((path, FilePath(stream.name)))
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            backups[path] = path.read_bytes() if path.exists() else None
        for path, temporary in staged:
            if not overwrite:
                # Link is atomic and fails if a concurrent writer created it.
                os.link(temporary, path)
                temporary.unlink()
            else:
                os.replace(temporary, path)
            written.append(path)
    except Exception:
        for path in reversed(written):
            if backups[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(backups[path])
        raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def post_gcode(job_name, file_path, post_name, arguments, overwrite, include_gcode):
    job = operation_job(job_name)
    validation = job_validation(job, recompute=True)
    if not validation["valid"]:
        raise ValueError(
            "CAM Job is not ready for posting: " + str(validation["issues"])
        )
    post_name, post = postprocessor_for(job, post_name)
    original_args = job.PostProcessorArgs if arguments is None else arguments
    args = noninteractive_post_args(post, original_args)
    output = file_path or job.PostProcessorOutputFile
    if not output:
        raise ValueError("Provide file_path or a Job output filename")
    records, outputs = [], []
    with temporary_post_settings(job, args, output):
        try:
            with complete_split_initialization(post, job):
                sections = post.export()
        except SystemExit as exc:
            raise ValueError(
                "Native postprocessor argument parsing failed: " + str(exc)
            ) from None
        if not sections:
            raise ValueError("Native postprocessor returned no sections")
        generator = FilenameGenerator(job)
        filenames = generator.generate_filenames()
        for index, section in enumerate(sections):
            if not isinstance(section, (tuple, list)) or len(section) != 2:
                raise ValueError("Invalid native postprocessor section")
            label, text = section
            # Native section names can be user labels; keep them in filenames,
            # not as subdirectories or relative traversals.
            component = (
                ""
                if label == "allitems"
                else re.sub(r"[^\w. -]", "_", str(label)).strip(". ")
            )
            generator.set_subpartname(component)
            path = FilePath(next(filenames)).absolute()
            if text is None:
                records.append(
                    {
                        "section": str(label),
                        "index": index,
                        "path": None,
                        "written": False,
                        "reason": "Native postprocessor requested no local file",
                    }
                )
                continue
            if not isinstance(text, str):
                raise ValueError(
                    "Native postprocessor did not return text for section " + str(label)
                )
            # FreeCAD's prefix signals literal LF output, not two program lines.
            if text.startswith("\n\n"):
                text = text[2:]
            elif "\r" not in text:
                text = text.replace("\n", os.linesep)
            data = text.encode("utf-8")
            outputs.append((path, data))
            record = {
                "section": str(label),
                "index": index,
                "path": str(path),
                "written": True,
                "bytes": len(data),
                "empty": not bool(data),
            }
            if include_gcode:
                record["gcode"] = text
            records.append(record)
    # Complete document changes before filesystem publication.
    if outputs:
        job.LastPostProcessOutput = str(outputs[-1][0])
        job.LastPostProcessDate = datetime.now(timezone.utc).isoformat()
    write_output_files(outputs, overwrite)
    return {
        "job": job.Name,
        "post_processor": post_name,
        "post_args": args,
        "split_output": job.SplitOutput,
        "order_output_by": job.OrderOutputBy,
        "sections": len(records),
        "files": records,
        "written_files": len(outputs),
        "path": str(outputs[0][0]) if len(outputs) == 1 else None,
        "bytes": sum(len(data) for _, data in outputs),
        "validation": validation,
        "status": "exported" if outputs else "no_local_output",
    }
