-- Development bootstrap only. Two roles: the owner runs migrations, the application has no
-- UPDATE/DELETE on the audit history (ADR-0004). Passwords here are development values.
CREATE ROLE campus_owner LOGIN PASSWORD 'campus_owner_dev';
CREATE ROLE campus_app LOGIN PASSWORD 'campus_app_dev';
-- S4: the vault role reads and writes protected evaluation material in schema `vault`; the application role has no access there.
CREATE ROLE campus_vault LOGIN PASSWORD 'campus_vault_dev';
CREATE DATABASE campus OWNER campus_owner;
CREATE DATABASE campus_test OWNER campus_owner;
