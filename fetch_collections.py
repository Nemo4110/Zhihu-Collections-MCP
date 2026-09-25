# -*- coding:utf-8 -*-
"""
独立的收藏夹获取脚本
从知乎页面获取用户的所有收藏夹，并更新到config.json文件中
"""
import os
import json
import requests
import time
import random
import logging
from datetime import datetime
import pathlib
import platform

# 收藏夹列表 API 的公共请求头和分页大小
from paths_config import get_current_os, load_config, load_cookies, parse_output_path

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.zhihu.com/collections/mine",
}
COLLECTIONS_PAGE_LIMIT = 20


def setup_logging():
    """设置日志"""
    # 获取日志目录
    logs_dir = os.path.join(os.path.dirname(__file__), 'downloads', 'logs')
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"openCollection_{timestamp}.log"
    log_path = os.path.join(logs_dir, log_filename)
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler()
        ],
        force=True
    )
    
    return log_path


def get_url_token(cookies=None):
    """
    通过知乎 API 获取当前登录用户的 url_token
    :param cookies: cookies字典
    :return: url_token 字符串，失败时返回 None
    """
    try:
        response = requests.get(
            "https://www.zhihu.com/api/v4/me",
            headers=API_HEADERS,
            cookies=cookies,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        url_token = data.get("url_token")
        if url_token:
            logging.info(f"获取到当前用户 url_token: {url_token}")
            return url_token

        logging.error(f"API 未返回用户信息，cookies 可能已过期: {str(data)[:200]}")
        return None
    except Exception as e:
        logging.error(f"获取用户信息失败（cookies 可能已过期）: {str(e)}")
        return None


def get_collections_from_page(page_num=1, cookies=None, url_token=None):
    """
    通过知乎收藏夹列表 API 获取收藏夹信息
    （旧版通过 /collections/mine 网页解析 SelfCollectionItem，页面改版后已失效）
    :param page_num: 页码
    :param cookies: cookies字典
    :param url_token: 当前用户 url_token
    :return: 收藏夹列表和是否有更多项目
    """
    if not url_token:
        logging.error("缺少 url_token，无法请求收藏夹列表 API")
        return [], False

    offset = (page_num - 1) * COLLECTIONS_PAGE_LIMIT
    url = f"https://www.zhihu.com/api/v4/people/{url_token}/collections?limit={COLLECTIONS_PAGE_LIMIT}&offset={offset}"

    try:
        response = requests.get(url, headers=API_HEADERS, cookies=cookies, timeout=15)
        response.raise_for_status()

        data = response.json()
        collection_items = data.get("data", [])
        collections = []

        for item in collection_items:
            # 收藏夹 id 和标题构成配置项
            collection_id = item.get("id")
            title = item.get("title")
            if collection_id and title:
                collections.append({
                    'name': title,
                    'url': f"https://www.zhihu.com/collection/{collection_id}"
                })

        return collections, len(collection_items) > 0

    except Exception as e:
        logging.error(f"获取第{page_num}页收藏夹失败: {str(e)}")
        return [], False


def get_all_collections(cookies=None):
    """
    获取所有收藏夹
    :param cookies: cookies字典
    :return: 所有收藏夹列表
    """
    url_token = get_url_token(cookies)
    if not url_token:
        logging.error("无法获取用户 url_token，请检查 cookies 是否过期")
        print("无法获取用户 url_token，请检查 cookies 是否过期")
        return []

    all_collections = []
    page = 1

    while True:
        logging.info(f"正在获取第{page}页收藏夹...")
        print(f"正在获取第{page}页收藏夹...")

        collections, has_items = get_collections_from_page(page, cookies, url_token)
        
        if not has_items:
            logging.info(f"第{page}页没有更多收藏夹，结束获取")
            print(f"第{page}页没有更多收藏夹，结束获取")
            break
            
        all_collections.extend(collections)
        logging.info(f"第{page}页获取到{len(collections)}个收藏夹")
        print(f"第{page}页获取到{len(collections)}个收藏夹")
        
        page += 1
        # 添加延时避免请求过快
        time.sleep(random.randint(1, 3))
    
    return all_collections


def update_config_with_collections(collections):
    """
    将获取到的收藏夹更新到config.json中
    :param collections: 收藏夹列表
    :return: 是否成功
    """
    try:
        # 读取当前配置
        config = load_config()
        if config is None:
            return False
        
        # 更新zhihuUrls
        config['zhihuUrls'] = collections
        
        # 将openCollection设为false，因为已经获取完成
        config['openCollection'] = False
        
        # 写回配置文件
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        logging.info(f"配置文件已更新，包含{len(collections)}个收藏夹")
        print(f"配置文件已更新，包含{len(collections)}个收藏夹")
        return True
        
    except Exception as e:
        logging.error(f"更新配置文件失败: {str(e)}")
        print(f"更新配置文件失败: {str(e)}")
        return False


def save_collections_log(collections, log_path):
    """
    保存收藏夹获取日志
    :param collections: 收藏夹列表
    :param log_path: 日志文件路径
    """
    try:
        # 生成详细日志
        log_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_collections": len(collections),
            "collections": collections,
            "log_file": log_path
        }
        
        # 保存到logs目录
        logs_dir = os.path.dirname(log_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_log_path = os.path.join(logs_dir, f"openCollection_{timestamp}.json")
        
        with open(json_log_path, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        
        logging.info(f"详细日志已保存到: {json_log_path}")
        print(f"详细日志已保存到: {json_log_path}")
        
    except Exception as e:
        logging.error(f"保存详细日志失败: {str(e)}")


def main():
    """主函数"""
    print("=" * 60)
    print("知乎收藏夹获取工具")
    print("=" * 60)
    
    # 设置日志
    log_path = setup_logging()
    logging.info("开始执行收藏夹获取任务")
    print(f"日志文件: {log_path}")
    
    # 加载cookies
    cookies = load_cookies()
    if not cookies:
        print("警告: 未找到有效的cookies，可能无法获取私密收藏夹")
        logging.warning("未找到有效的cookies")
    
    # 获取所有收藏夹
    print("\n开始获取收藏夹列表...")
    logging.info("开始获取收藏夹列表")
    
    try:
        all_collections = get_all_collections(cookies)
        
        if not all_collections:
            print("未获取到任何收藏夹")
            logging.warning("未获取到任何收藏夹")
            return False
        
        print(f"\n总共获取到 {len(all_collections)} 个收藏夹:")
        logging.info(f"总共获取到 {len(all_collections)} 个收藏夹")
        
        # 显示获取到的收藏夹
        for i, collection in enumerate(all_collections, 1):
            print(f"  {i}. {collection['name']}")
            logging.info(f"收藏夹 {i}: {collection['name']} - {collection['url']}")
        
        # 更新配置文件
        print(f"\n正在更新config.json...")
        success = update_config_with_collections(all_collections)
        
        if success:
            print("✓ 配置文件更新成功")
            print("✓ openCollection已自动设为false")
            logging.info("配置文件更新成功")
            
            # 保存详细日志
            save_collections_log(all_collections, log_path)
            
            print(f"\n下一步:")
            print("1. 运行 python main.py 开始下载收藏夹内容")
            print("2. 如需重新获取收藏夹列表，将config.json中的openCollection设为true后重新运行此脚本")
            
            return True
        else:
            print("✗ 配置文件更新失败")
            logging.error("配置文件更新失败")
            return False
            
    except Exception as e:
        error_msg = f"获取收藏夹过程中发生错误: {str(e)}"
        print(f"✗ {error_msg}")
        logging.error(error_msg)
        return False


if __name__ == '__main__':
    success = main()
    if success:
        print(f"\n{'=' * 60}")
        print("收藏夹获取完成！")
        print(f"{'=' * 60}")
    else:
        print(f"\n{'=' * 60}")
        print("收藏夹获取失败！")
        print(f"{'=' * 60}")
        exit(1)