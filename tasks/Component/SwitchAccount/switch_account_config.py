from pydantic import Field, BaseModel

from module.logger import logger
from tasks.Component.config_base import DateTime


class AccountInfo(BaseModel):
    """
        character:   角色名字
        svr:         角色所在服务器
        account:     账号
        appleOrAndroid:  角色所属平台 安卓/苹果(可选)
                False           Apple
                True            Android
    """
    character: str = Field(default="", description='character_help')
    svr: str = Field(default="", description="svr_help")
    account: str = Field(default="", description="account_help")
    # 为防止ocr出错 暂定格式 字符串以#分割
    account_alias: str = Field(default="", description="account_alias_help")
    apple_or_android: bool = Field(default=True, description="apple_or_android_help")
    # 上一次执行成功的时间 ,防止出错时重复登录浪费时间
    last_complete_time: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description="last_complete_time_help")

    def is_account_alias(self, ocr_account):
        tmp_account = AccountInfo.preprocessAccount(self.account)
        if not tmp_account:
            # 没填邮箱的账号保持原行为(任何行都算命中), 由上层决定要不要用
            return True
        if ocr_account == self.account:
            return True
        if ocr_account.startswith(tmp_account):
            # 前缀相同不代表是同一个账号: 目标 solomondevil1 会命中 solomondevil11@163.com,
            # 点下去实际登录的是 11 号邮箱, 角色列表里当然找不到目标角色(11-22 实测 8 个号失败)。
            # 前缀之后紧跟数字 => 这是另一个更长的账号, 不算匹配; 紧跟 @ 或为空
            # (OCR 把 @163.com 读丢) 才认。
            rest = ocr_account[len(tmp_account):]
            if not rest[:1].isdigit():
                return True
            logger.warning(
                '账号前缀命中但判定为另一个账号: OCR=%s 目标=%s', ocr_account, self.account
            )
        if not self.account_alias:
            return False
        _accountAliasList = self.account_alias.split('#')
        for alias in _accountAliasList:
            if ocr_account.startswith(alias):
                return True
        return False

    @staticmethod
    def preprocessAccount(account: str):
        """
            预处理账号信息 便于比对
            邮箱账号        去除@后面的部分 防止@被识别为其他
        @param account:
        @type account:
        @return:
        @rtype:
        """
        return account.split('@')[0]

    def is_valid(self):
        return self.character!="" and self.character is not None