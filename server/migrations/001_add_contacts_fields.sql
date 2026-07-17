-- 迁移脚本：为用户表添加飞书通讯录属性字段
-- 执行方式：mysql -u lanshan -p lanshan_ai_agent < migrations/001_add_contacts_fields.sql

ALTER TABLE users
ADD COLUMN department_ids VARCHAR(512) DEFAULT NULL COMMENT '飞书部门ID列表（逗号分隔）',
ADD COLUMN job_level_id VARCHAR(64) DEFAULT NULL COMMENT '飞书职级ID',
ADD COLUMN employee_type VARCHAR(64) DEFAULT NULL COMMENT '飞书员工类型';

-- 更新现有用户的默认值
UPDATE users SET department_ids = '' WHERE department_ids IS NULL;
UPDATE users SET job_level_id = '' WHERE job_level_id IS NULL;
UPDATE users SET employee_type = '' WHERE employee_type IS NULL;

-- 添加索引（可选，根据实际查询需求）
-- CREATE INDEX idx_users_department_ids ON users(department_ids);