{% macro analytics_iceberg_properties(domain, partitioning=none) -%}
    {%- set expected_schema = 'analytics.' ~ domain -%}
    {%- if this.schema != expected_schema -%}
        {{ exceptions.raise_compiler_error(
            "Analytics model " ~ this.identifier ~ " must publish to " ~ expected_schema
            ~ ", received " ~ this.schema
        ) }}
    {%- endif -%}
    {%- set properties = {
        'format': "'PARQUET'",
        'format_version': '2'
    } -%}
    {%- if partitioning is not none -%}
        {%- do properties.update({'partitioning': "ARRAY['" ~ partitioning ~ "']"}) -%}
    {%- endif -%}
    {{ return(properties) }}
{%- endmacro %}
