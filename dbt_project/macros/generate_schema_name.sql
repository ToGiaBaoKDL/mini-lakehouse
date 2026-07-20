-- dbt_project/macros/generate_schema_name.sql
-- Ghi đè macro mặc định để dbt không tự ghép target.schema (ví dụ 'default_') vào trước custom schema name
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
