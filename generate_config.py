import json
import requests

SOURCE_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.json"
OUTPUT_FILE = "xray-config.json"

# 限制导入的最大节点数量，防止节点过多导致系统句柄耗尽 (FD leak) 或 Xray 卡死
MAX_PROXIES = 100

def fetch_proxies():
    resp = requests.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # 优先选取较新的节点并做截断
    return data[:MAX_PROXIES] if isinstance(data, list) else []

def build_xray_config(proxies):
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

    outbounds = []
    for idx, item in enumerate(proxies):
        protocol = item.get("protocol")
        ip = item.get("ip")
        port = item.get("port")

        if protocol == "http":
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "http",
                "settings": {
                    "servers": [{"address": ip, "port": port}]
                }
            }
        elif protocol in ("socks5", "socks4"):
            version = 4 if protocol == "socks4" else 5
            outbound = {
                "tag": f"proxy-{idx}",
                "protocol": "socks",
                "settings": {
                    "servers": [
                        {
                            "address": ip,
                            "port": port,
                            "users": []
                        }
                    ],
                    "version": version
                },
                "streamSettings": {
                    "sockopt": {
                        "tcpFastOpen": False  # 避免部分系统环境未支持 TFO 导致握手超时
                    }
                }
            }
        else:
            continue

        outbounds.append(outbound)

    # 直连兜底通道
    outbounds.append({
        "tag": "direct",
        "protocol": "freedom"
    })

    # 关键修复 1：定义健康检查与探测模块，解决核心依赖解析失败问题
    observatory = {
        "subjectSelector": ["proxy-"],
        "probeUrl": "https://www.google.com/generate_204",
        "probeInterval": "3m",
        "enableConcurrency": True
    }

    # 关键修复 2：使用 leastPing 代替 random，剔除失效死节点并优先低延迟
    balancers = [
        {
            "tag": "proxy-balancer",
            "selector": ["proxy-"],
            "strategy": {
                "type": "leastPing"
            },
            "fallbackTag": "direct"
        }
    ]

    routing = {
        "rules": [
            {
                "inboundTag": ["socks-in", "http-in"],
                "balancerTag": "proxy-balancer"
            }
        ],
        "balancers": balancers
    }

    config = {
        "inbounds": inbounds,
        "outbounds": outbounds,
        "observatory": observatory,
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
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        print(f"Success: Config written to {OUTPUT_FILE} with {len(proxies)} proxies.")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
