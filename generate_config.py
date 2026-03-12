#!/usr/bin/env python3
"""根据订阅 URL 下载节点并生成 Clash/Mihomo 配置文件。

脚本使用 Clash UA 下载每个订阅 URL 的配置，提取其中的代理节点，
然后注入到模板中，生成最终配置。不依赖 proxy-provider 机制。
"""

import argparse
import gzip
import re
import ssl
import sys
import urllib.request
import zlib
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = SCRIPT_DIR / "clash_config_template.yaml"
DEFAULT_OUTPUT = SCRIPT_DIR / "clash_config.yaml"

CLASH_UA = "clash-verge/v1.7.7"
HK_FILTER = re.compile(r"(?i)港|HK|Hong Kong|🇭🇰")

MARKER_ALL = "# __INJECT_ALL__"
MARKER_NOHK = "# __INJECT_NOHK__"


class _ClashDumper(yaml.SafeDumper):
    """Indents list items within mappings for Clash-style YAML."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


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
            "根据订阅 URL 下载节点并生成 Clash/Mihomo 配置文件。\n"
            "脚本会下载每个 URL 对应的配置，提取代理节点后注入到模板中。"
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


def download_subscription(url):
    """使用 Clash UA 下载订阅配置并返回解析后的 YAML。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": CLASH_UA,
        "Accept": "*/*",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        data = resp.read()
        encoding = resp.headers.get("Content-Encoding", "")
        if encoding == "gzip":
            data = gzip.decompress(data)
        elif encoding == "deflate":
            data = zlib.decompress(data)
        raw = data.decode("utf-8")
    return yaml.safe_load(raw)


def extract_proxies(config_data):
    """从下载的 Clash 配置中提取 proxies 列表。"""
    if not isinstance(config_data, dict):
        return []
    return config_data.get("proxies") or config_data.get("Proxy") or []


def fetch_all_proxies(urls):
    """下载所有订阅并合并去重节点。"""
    all_proxies = []
    seen = set()
    for url in urls:
        print(f"  下载: {url[:80]}...")
        try:
            data = download_subscription(url)
            proxies = extract_proxies(data)
            added = 0
            for p in proxies:
                name = p.get("name", "")
                if name and name not in seen:
                    seen.add(name)
                    all_proxies.append(p)
                    added += 1
            print(f"    获取 {len(proxies)} 个节点，新增 {added} 个")
        except Exception as e:
            print(f"    失败: {e}", file=sys.stderr)
    return all_proxies


def dump_proxies_block(proxies):
    """将 proxies 列表序列化为 Clash 风格的 YAML 文本块。"""
    raw = yaml.dump(
        proxies, Dumper=_ClashDumper,
        allow_unicode=True, default_flow_style=False,
        sort_keys=False, width=1000,
    )
    indented = "\n".join(
        "  " + line if line.strip() else ""
        for line in raw.rstrip("\n").splitlines()
    )
    return f"proxies:\n{indented}"


def build_name_lines(names, indent):
    """构建 proxy-group 中 proxies 列表的 YAML 行。"""
    prefix = " " * indent
    return "\n".join(f"{prefix}- {name}" for name in names)


def generate_config(template_text, proxies):
    """用下载的节点替换模板中的占位符，生成最终配置文本。"""
    all_names = [p["name"] for p in proxies]
    nohk_names = [n for n in all_names if not HK_FILTER.search(n)]

    text = template_text.replace("proxies: []", dump_proxies_block(proxies), 1)

    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped == MARKER_ALL:
            indent = len(line) - len(line.lstrip())
            text = text.replace(line, build_name_lines(all_names, indent), 1)
        elif stripped == MARKER_NOHK:
            indent = len(line) - len(line.lstrip())
            text = text.replace(line, build_name_lines(nohk_names, indent), 1)

    if not text.endswith("\n"):
        text += "\n"
    return text


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

    for marker in (MARKER_ALL, MARKER_NOHK):
        if marker not in template_text:
            print(f"错误: 模板中未找到占位标记: {marker}", file=sys.stderr)
            sys.exit(1)

    print("正在下载订阅并提取节点...")
    proxies = fetch_all_proxies(urls)

    if not proxies:
        print("错误: 未获取到任何代理节点", file=sys.stderr)
        sys.exit(1)

    nohk_count = sum(1 for p in proxies if not HK_FILTER.search(p["name"]))
    print(f"\n共获取 {len(proxies)} 个唯一节点 (非港区 {nohk_count} 个)")

    result = generate_config(template_text, proxies)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result, encoding="utf-8")

    print(f"已生成配置文件: {output_path}")


if __name__ == "__main__":
    main()
