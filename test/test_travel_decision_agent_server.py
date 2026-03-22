import os
import sys

import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from create_logger import logger


def main():
    base_url = "http://localhost:5005"

    try:
        metadata = httpx.get(f"{base_url}/metadata", timeout=10).json()
        logger.info("获取 TravelDecisionAgent 信息")
        logger.info(f"名称: {metadata['name']}")
        logger.info(f"描述: {metadata['description']}")
        logger.info(f"版本: {metadata['version']}")
        if metadata.get("skills"):
            logger.info("支持技能:")
            for skill in metadata["skills"]:
                logger.info(f"- {skill['name']}: {skill['description']}")
                if skill.get("examples"):
                    logger.info(f"  示例: {', '.join(skill['examples'])}")
    except Exception as exc:
        logger.error(f"无法获取 TravelDecisionAgent 信息: {exc}")

    while True:
        user_input = input("输入您的查询（天气/时间/票务，输入 'exit' 退出）：").strip()
        if user_input.lower() == "exit":
            break
        if not user_input:
            continue

        try:
            response = httpx.post(f"{base_url}/invoke", json={"text": user_input}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            print(payload["text"])
        except Exception as exc:
            logger.error(f"查询失败: {exc}")


if __name__ == "__main__":
    print("TravelDecisionAgent 查询客户端测试脚本")
    main()
