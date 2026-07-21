{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if node.resource_type == 'test' -%}
        {{ target.schema }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {%- set schema_name = custom_schema_name | trim -%}
        {%- if not schema_name.startswith('analytics.') -%}
            {{ exceptions.raise_compiler_error(
                "Custom schemas must be explicit analytics.<domain> namespaces; received "
                ~ schema_name ~ " for " ~ node.name
            ) }}
        {%- endif -%}
        {{ schema_name }}
    {%- endif -%}
{%- endmacro %}
