{% macro generate_schema_name(custom_schema_name, node) -%}
    {# Environment isolation belongs at the Polaris catalog level, not in namespace names. #}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
