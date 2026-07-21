{% macro analytics_iceberg_properties(domain, partitioning=none) -%}
    {%- set analytics_uri = env_var('LAKEHOUSE_STORAGE__ANALYTICS_URI', 's3://analytics').rstrip('/') -%}
    {%- set expected_schema = 'analytics.' ~ domain -%}
    {%- if this.schema != expected_schema -%}
        {{ exceptions.raise_compiler_error(
            "Analytics model " ~ this.identifier ~ " must publish to " ~ expected_schema
            ~ ", received " ~ this.schema
        ) }}
    {%- endif -%}
    {%- set properties = {
        'format': "'PARQUET'",
        'format_version': '2',
        'location': "'" ~ analytics_uri ~ "/" ~ domain ~ "/" ~ this.identifier ~ "'"
    } -%}
    {%- if partitioning is not none -%}
        {%- do properties.update({'partitioning': "ARRAY['" ~ partitioning ~ "']"}) -%}
    {%- endif -%}
    {{ return(properties) }}
{%- endmacro %}
