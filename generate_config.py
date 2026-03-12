#!/usr/bin/env python3
"""根据订阅 URL 列表和模板生成 Clash/Mihomo 配置文件。"""

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR / "clash_config_template.yaml"
DEFAULT_OUTPUT = SCRIPT_DIR / "clash_config.yaml"


def parse_args():
    epilog = """\
示例:
  # 交互式输入 URL
  python generate_config.py

  # 传入多个 URL
  python generate_config.py "https://sub1.com/link" "https://sub2.com/link"

  # 指定模板和输出路径
  python generate_config.py -t my_template.yaml -o out.yaml "https://sub1.com/link"
"""
    parser = argparse.ArgumentParser(
        description=(
            "根据订阅 URL 列表和模板生成 Clash/Mihomo 配置文件。\n"
            "脚本会自动为每个 URL 生成 proxy-provider 并更新 proxy-groups 引用。"
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="订阅 URL，可传入一个或多个。未提供时进入交互式输入。",
    )
    parser.add_argument(
        "-t", "--template",
        default=str(DEFAULT_TEMPLATE),
        help="模板文件路径 (默认: 脚本同目录/clash_config_template.yaml)",
    )
    parser.add_argument(
        "-o", "--output",
        default=str(DEFAULT_OUTPUT),
        help="输出文件路径 (默认: 脚本同目录/clash_config.yaml)",
    )
    return parser.parse_args()


def collect_urls_interactive():
    print("请逐行输入订阅 URL（输入空行结束）:")
    urls = []
    while True:
        try:
            line = input(f"  URL {len(urls) + 1}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            break
        urls.append(line)
    return urls


def find_provider_section(lines):
    """Return [start, end) line indices for the proxy-providers section content."""
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^proxy-providers\s*:", line):
            start = i
            break
    if start is None:
        raise ValueError("模板中未找到 proxy-providers 区块")

    end = start + 1
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i][0].isspace():
            end = i
            break
    else:
        end = len(lines)

    while end > start + 1 and not lines[end - 1].strip():
        end -= 1

    return start, end


def parse_old_provider_names(lines, start, end):
    """Extract provider names defined in the section."""
    names = []
    for i in range(start + 1, end):
        m = re.match(r"^  (\S+)\s*:", lines[i])
        if m:
            names.append(m.group(1))
    return names


def extract_provider_pattern(lines, start, end):
    """Extract the first provider block as a reusable pattern."""
    block_start = None
    for i in range(start + 1, end):
        if re.match(r"^  \S+\s*:", lines[i]):
            block_start = i
            break
    if block_start is None:
        raise ValueError("模板中未找到 provider 定义块")

    block_end = block_start + 1
    for i in range(block_start + 1, end):
        if re.match(r"^  \S+\s*:", lines[i]):
            block_end = i
            break
    else:
        block_end = end

    while block_end > block_start + 1 and not lines[block_end - 1].strip():
        block_end -= 1

    return lines[block_start:block_end]


def build_provider_block(pattern_lines, name, url):
    """Produce a concrete provider block by substituting name, url and path."""
    result = []
    for line in pattern_lines:
        if re.match(r"^  \S+\s*:", line):
            result.append(f"  {name}:")
        elif re.match(r"^    url\s*:", line):
            result.append(f'    url: "{url}"')
        elif re.match(r"^    path\s*:", line) and "providers/" in line:
            result.append(f"    path: ./providers/{name}.yaml")
        else:
            result.append(line)
    return result


def replace_use_lists(text, old_names, new_names):
    """Replace every use: list that references old providers with new ones."""
    old_alt = "|".join(re.escape(n) for n in old_names)
    pattern = re.compile(
        r"([ \t]+use:\n)"
        r"((?:([ \t]+)- (?:" + old_alt + r")\n)"
        r"(?:[ \t]+- (?:" + old_alt + r")\n)*)"
    )

    def _replacer(m):
        indent = m.group(3)
        return m.group(1) + "".join(f"{indent}- {n}\n" for n in new_names)

    return pattern.sub(_replacer, text)


def generate_config(template_text, urls):
    lines = template_text.splitlines()

    pp_start, pp_end = find_provider_section(lines)
    old_names = parse_old_provider_names(lines, pp_start, pp_end)
    pattern = extract_provider_pattern(lines, pp_start, pp_end)

    new_names = [f"provider-{i + 1}" for i in range(len(urls))]

    section = ["proxy-providers:"]
    for i, (name, url) in enumerate(zip(new_names, urls)):
        section.extend(build_provider_block(pattern, name, url))
        if i < len(urls) - 1:
            section.append("")

    result_lines = lines[:pp_start] + section + lines[pp_end:]
    text = "\n".join(result_lines)
    if not text.endswith("\n"):
        text += "\n"

    text = replace_use_lists(text, old_names, new_names)
    return text, new_names


def main():
    args = parse_args()
    urls = args.urls or collect_urls_interactive()

    if not urls:
        print("错误: 未提供任何订阅 URL", file=sys.stderr)
        sys.exit(1)

    template_path = Path(args.template)
    if not template_path.exists():
        print(f"错误: 模板文件不存在: {template_path}", file=sys.stderr)
        sys.exit(1)

    template_text = template_path.read_text(encoding="utf-8")
    result, new_names = generate_config(template_text, urls)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result.encode("utf-8"))

    print(f"已生成配置文件: {output_path}")
    print(f"  providers: {', '.join(new_names)}")
    print(f"  URL 数量: {len(urls)}")


if __name__ == "__main__":
    main()
