import json
import requests

# 代理数据源
SOURCE_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.json"

# 输出文件名
OUTPUT_FILE = "xray-config.json"

def fetch_proxies():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    return resp.json()

def build_xray_config(proxies):
    # 定义入站（可根据需要修改端口）
    inbounds = [
        {
            "port": 1080,
            "protocol": "socks",
            "tag": "socks-in",
            "settings": {"udp": True}
        },
        {
            "port": 1081,
            "protocol": "http",
            "tag": "http-in"
        }
    ]

    # 构建出站列表
    outbounds = []
    for idx, item in enumerate(proxies):
        proxy_url = item["proxy"]
        protocol = item["protocol"]
        ip = item["ip"]
        port = item["port"]

        # 解析协议（可能为 http, socks5, socks4）
        if protocol == "http":
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "http",
                "settings": {
                    "servers": [
                        {"address": ip, "port": port}
                    ]
                }
            }
        elif protocol in ("socks5", "socks4"):
            # Xray socks 出站支持 version 字段：4 或 5
            version = 4 if protocol == "socks4" else 5
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "socks",
                "settings": {
                    "servers": [
                        {
                            "address": ip,
                            "port": port,
                            "users": []   # 无认证
                        }
                    ]
                },
                # 添加额外配置指定 socks 版本
                "streamSettings": {
                    "sockopt": {
                        "tcpFastOpen": True
                    }
                }
            }
            # 通过 version 指定，但 Xray 的 socks 出站默认是 5，需用 "version" 字段（在 settings 层级）
            # 注意：Xray 文档中 socks 出站 settings 里有 "version"，但实际配置中可能直接加在 settings 下
            outbound["settings"]["version"] = version
        else:
            # 未知协议跳过
            continue

        outbounds.append(outbound)

    # 添加一个直连 fallback
    outbounds.append({
        "tag": "direct",
        "protocol": "freedom"
    })

    # 负载均衡器配置（随机选择）
    balancers = [
        {
            "tag": "random-balancer",
            "selector": ["proxy-"],          # 匹配所有 proxy- 开头的标签
            "strategy": {"type": "random"},
            "fallbackTag": "direct"
        }
    ]

    # 路由规则：将所有入站流量交给负载均衡器
    routing = {
        "rules": [
            {
                "inboundTag": ["socks-in", "http-in"],
                "balancerTag": "random-balancer"
            }
        ],
        "balancers": balancers
    }

    # 组装完整配置
    config = {
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": routing
    }
    return config

def main():
    try:
        proxies = fetch_proxies()
        if not proxies:
            print("No proxies fetched, exiting.")
            return
        config = build_xray_config(proxies)
        with open(OUTPUT_FILE, "w") as f:
            json.dump(config, f, indent=2)
        print(f"Config written to {OUTPUT_FILE} with {len(proxies)} proxies.")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
