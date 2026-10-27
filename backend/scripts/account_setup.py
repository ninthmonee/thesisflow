"""Prepare or show the one-time first-owner token in the local console only."""

import argparse

from tradingagents.web.application.authentication import AuthenticationService
from tradingagents.web.infrastructure.database import initialize_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--show-token", action="store_true", help="Show in this local terminal; never publish or paste into chat")
    parser.add_argument("--rotate", action="store_true", help="Invalidate an unused first-owner token and create a replacement")
    args = parser.parse_args()
    database = initialize_database(args.database)
    try:
        service = AuthenticationService(database)
        path = service.ensure_setup_token(rotate=args.rotate)
        if path is None:
            print("管理员已初始化，请使用登录页面。")
        elif args.show_token:
            print(path.read_text(encoding="utf-8").strip())
        else:
            print(f"一次性管理员初始化令牌保存在本机私有文件：{path}")
            print("如需在当前本机控制台显示，加 --show-token；请勿把令牌提交到代码仓库。")
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
