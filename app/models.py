"""
数据库模型定义
定义所有数据库表的 SQLAlchemy 模型
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.utils.time_utils import get_now


class Team(Base):
    """Team 信息表"""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, comment="Team 管理员邮箱")
    access_token_encrypted = Column(Text, nullable=False, comment="加密存储的 AT")
    refresh_token_encrypted = Column(Text, comment="加密存储的 RT")
    session_token_encrypted = Column(Text, comment="加密存储的 Session Token")
    client_id = Column(String(100), comment="OAuth Client ID")
    encryption_key_id = Column(String(50), comment="加密密钥 ID")
    account_id = Column(String(100), comment="当前使用的 account-id")
    team_name = Column(String(255), comment="Team 名称")
    plan_type = Column(String(50), comment="计划类型")
    subscription_plan = Column(String(100), comment="订阅计划")
    expires_at = Column(DateTime, comment="订阅到期时间")
    current_members = Column(Integer, default=0, comment="当前成员数")
    pending_invites = Column(Integer, default=0, comment="待接受邀请数")
    max_members = Column(Integer, default=5, comment="最大成员数")
    status = Column(String(20), default="active", comment="状态: active/full/expired/error/banned")
    account_role = Column(String(50), comment="账号角色: account-owner/standard-user 等")
    device_code_auth_enabled = Column(Boolean, default=False, comment="是否开启设备代码身份验证")
    error_count = Column(Integer, default=0, comment="连续报错次数")
    source_type = Column(String(20), default="local", comment="来源类型: local/cpa")
    cpa_service_id = Column(Integer, comment="关联的 CPA 服务 ID")
    cpa_mother_account_id = Column(Integer, comment="关联的 CPA 母号 ID")
    cpa_auth_file_name = Column(String(255), comment="关联的 CPA Auth File 名称")
    sync_status = Column(String(50), default="idle", comment="同步状态: idle/ready/error/upstream_missing")
    sync_error = Column(Text, comment="同步错误信息")
    last_upstream_refresh_at = Column(DateTime, comment="上游最后刷新时间")
    last_sync = Column(DateTime, comment="最后同步时间")
    created_at = Column(DateTime, default=get_now, comment="创建时间")

    # 关系
    team_accounts = relationship("TeamAccount", back_populates="team", cascade="all, delete-orphan")
    redemption_records = relationship("RedemptionRecord", back_populates="team", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index("idx_status", "status"),
    )


class TeamAccount(Base):
    """Team Account 关联表"""
    __tablename__ = "team_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String(100), nullable=False, comment="Account ID")
    account_name = Column(String(255), comment="Account 名称")
    is_primary = Column(Boolean, default=False, comment="是否为主 Account")
    created_at = Column(DateTime, default=get_now, comment="创建时间")

    # 关系
    team = relationship("Team", back_populates="team_accounts")

    # 唯一约束
    __table_args__ = (
        Index("idx_team_account", "team_id", "account_id", unique=True),
    )


class RedemptionCode(Base):
    """兑换码表"""
    __tablename__ = "redemption_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False, comment="兑换码")
    status = Column(String(20), default="unused", comment="状态: unused/used/expired/warranty_active")
    created_at = Column(DateTime, default=get_now, comment="创建时间")
    expires_at = Column(DateTime, comment="过期时间")
    used_by_email = Column(String(255), comment="使用者邮箱")
    used_team_id = Column(Integer, ForeignKey("teams.id"), comment="使用的 Team ID")
    used_at = Column(DateTime, comment="使用时间")
    has_warranty = Column(Boolean, default=False, comment="是否为质保兑换码")
    warranty_days = Column(Integer, default=30, comment="质保时长(天)")
    warranty_expires_at = Column(DateTime, comment="质保到期时间(首次使用后根据质保时长计算)")

    # 关系
    redemption_records = relationship("RedemptionRecord", back_populates="redemption_code")

    # 索引
    __table_args__ = (
        Index("idx_code_status", "code", "status"),
    )


class RedemptionRecord(Base):
    """使用记录表"""
    __tablename__ = "redemption_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, comment="用户邮箱")
    code = Column(String(32), ForeignKey("redemption_codes.code"), nullable=False, comment="兑换码")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, comment="Team ID")
    account_id = Column(String(100), nullable=False, comment="Account ID")
    redeemed_at = Column(DateTime, default=get_now, comment="兑换时间")
    is_warranty_redemption = Column(Boolean, default=False, comment="是否为质保兑换")

    # 关系
    team = relationship("Team", back_populates="redemption_records")
    redemption_code = relationship("RedemptionCode", back_populates="redemption_records")

    # 索引
    __table_args__ = (
        Index("idx_email", "email"),
    )


class Setting(Base):
    """系统设置表"""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, comment="配置项名称")
    value = Column(Text, comment="配置项值")
    description = Column(String(255), comment="配置项描述")
    created_at = Column(DateTime, default=get_now, comment="创建时间")
    updated_at = Column(DateTime, default=get_now, onupdate=get_now, comment="更新时间")

    # 索引
    __table_args__ = (
        Index("idx_key", "key"),
    )


class CPAService(Base):
    """CPA 服务配置表"""
    __tablename__ = "cpa_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, comment="CPA 服务名称")
    api_url = Column(String(500), nullable=False, comment="管理 API 地址")
    api_token_encrypted = Column(Text, nullable=False, comment="加密后的管理 API Token")
    proxy = Column(String(500), comment="服务级代理地址")
    enabled = Column(Boolean, default=True, comment="是否启用")
    last_tested_at = Column(DateTime, comment="最近测试时间")
    last_test_status = Column(String(20), comment="最近测试状态: success/error")
    last_test_message = Column(Text, comment="最近测试信息")
    created_at = Column(DateTime, default=get_now, comment="创建时间")
    updated_at = Column(DateTime, default=get_now, onupdate=get_now, comment="更新时间")

    mother_accounts = relationship("CPAMotherAccount", back_populates="service", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_cpa_services_name", "name"),
        Index("idx_cpa_services_enabled", "enabled"),
    )


class CPAMotherAccount(Base):
    """CPA 选中的母号表"""
    __tablename__ = "cpa_mother_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_id = Column(Integer, ForeignKey("cpa_services.id", ondelete="CASCADE"), nullable=False)
    auth_file_name = Column(String(255), nullable=False, comment="上游 auth-file 文件名")
    provider = Column(String(50), nullable=False, default="codex", comment="提供商")
    email = Column(String(255), comment="展示邮箱")
    label = Column(String(255), comment="展示标签")
    source = Column(String(50), comment="来源: file/memory")
    runtime_only = Column(Boolean, default=False, comment="是否仅运行时存在")
    selected = Column(Boolean, default=True, comment="是否被选为母号")
    upstream_status = Column(String(50), comment="上游状态")
    upstream_status_message = Column(Text, comment="上游状态描述")
    upstream_missing = Column(Boolean, default=False, comment="上游是否已丢失/重命名")
    access_token_encrypted = Column(Text, comment="加密后的 AT 缓存")
    refresh_token_encrypted = Column(Text, comment="加密后的 RT 缓存")
    session_token_encrypted = Column(Text, comment="加密后的 ST 缓存")
    client_id = Column(String(100), comment="镜像缓存的 Client ID")
    last_upstream_refresh_at = Column(DateTime, comment="上游最近刷新时间")
    last_sync = Column(DateTime, comment="最近同步时间")
    sync_status = Column(String(50), default="idle", comment="同步状态")
    sync_error = Column(Text, comment="同步错误信息")
    created_at = Column(DateTime, default=get_now, comment="创建时间")
    updated_at = Column(DateTime, default=get_now, onupdate=get_now, comment="更新时间")

    service = relationship("CPAService", back_populates="mother_accounts")

    __table_args__ = (
        Index("idx_cpa_mother_service_file", "service_id", "auth_file_name", unique=True),
        Index("idx_cpa_mother_selected", "selected"),
    )
