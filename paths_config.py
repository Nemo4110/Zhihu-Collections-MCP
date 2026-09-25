# -*- coding:utf-8 -*-
"""配置与跨平台路径模块。

接缝说明：本模块只做"文件/路径 → 数据"的转换，不做网络请求，
也不依赖知乎逻辑。调用方（main / fetch_collections / cli）通过
显式参数注入文件位置，默认值维持历史行为（当前工作目录）。
"""
import json
import logging
import os
import pathlib
import platform


def get_current_os():
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        return "unknown"


def parse_output_path(path_str, os_type):
    """解析输出路径，按操作系统类型归一化为绝对 Path。空输入返回 None。"""
    if not path_str:
        return None

    # 如果没有指定os，则自动检测
    if not os_type:
        os_type = get_current_os()

    try:
        if os_type.lower() == "windows":
            # Windows路径处理
            # 支持 D:\path\to\folder 或 D:/path/to/folder 格式
            path_str = path_str.replace('/', '\\')
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["linux", "freebsd", "openbsd", "netbsd", "solaris", "aix"]:
            # Unix-like系统路径处理
            # 支持 /usr/local/lib 格式
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["macos", "darwin"]:
            # macOS路径处理
            # 支持 /Users/username/Documents 或 ~/Documents 格式
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
        elif os_type.lower() in ["cygwin", "msys"]:
            # Cygwin/MSYS环境路径处理
            # 支持 /cygdrive/c/path 或 /c/path 格式
            if path_str.startswith('/cygdrive/'):
                # Cygwin格式: /cygdrive/c/path -> C:\path
                drive_path = path_str[10:]  # 移除 /cygdrive/
                if len(drive_path) >= 2 and drive_path[1] == '/':
                    path_str = drive_path[0].upper() + ':' + drive_path[1:].replace('/', '\\')
            elif path_str.startswith('/') and len(path_str) >= 3 and path_str[2] == '/':
                # MSYS格式: /c/path -> C:\path
                path_str = path_str[1].upper() + ':' + path_str[2:].replace('/', '\\')
            return pathlib.Path(path_str).resolve()
        else:
            # 其他系统，尝试通用处理
            logging.warning(f"未知操作系统类型: {os_type}，尝试通用路径处理")
            if path_str.startswith('~'):
                path_str = os.path.expanduser(path_str)
            return pathlib.Path(path_str).resolve()
    except (OSError, RuntimeError) as exc:
        logging.error(f"解析输出路径失败: {path_str} ({os_type}): {exc}")
        return None


def load_config(config_file='config.json', legacy_file='zhihuUrls.json'):
    """加载配置文件；缺失时回退旧版 zhihuUrls.json，再缺失返回空配置。"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("未找到config.json文件，尝试读取旧版zhihuUrls.json文件")
        try:
            with open(legacy_file, 'r', encoding='utf-8') as f:
                urls = json.load(f)
                return {"zhihuUrls": urls, "outputPath": "", "os": ""}
        except FileNotFoundError:
            print("未找到配置文件，请创建config.json文件并配置收藏夹信息")
            return {"zhihuUrls": [], "outputPath": "", "os": ""}


def load_cookies(cookie_file=None):
    """加载知乎会话 Cookie；文件缺失或格式错误时返回空 dict（匿名模式）。"""
    cookie_path = pathlib.Path(
        cookie_file or os.environ.get("ZHIHU_COOKIES_FILE", "cookies.json")
    )
    try:
        with cookie_path.open('r', encoding='utf-8') as f:
            cookies_list = json.load(f)
        cookies_dict = {}
        for cookie in cookies_list:
            cookies_dict[cookie['name']] = cookie['value']
        return cookies_dict
    except FileNotFoundError:
        print("未找到cookies.json文件，将使用无登录模式访问（部分内容可能无法获取）")
        return {}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"cookies文件格式无效，将使用无登录模式访问: {exc}")
        return {}
