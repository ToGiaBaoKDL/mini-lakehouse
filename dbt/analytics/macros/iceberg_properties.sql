{% macro analytics_iceberg_properties(partitioning=none) -%}
    {%- set analytics_uri = env_var('LAKEHOUSE_STORAGE__ANALYTICS_URI', 's3://analytics').rstrip('/') -%}
    {%- set namespace = this.schema | string -%}
    {%- if not namespace.startswith('analytics.') -%}
        {{ exceptions.raise_compiler_error(
            "Analytics models must publish below analytics.*, received " ~ namespace
        ) }}
    {%- endif -%}
    {%- set domain_path = namespace[10:] | replace('.', '/') -%}
    {%- set properties = {
        'format': "'PARQUET'",
        'format_version': '2',
        'location': "'" ~ analytics_uri ~ "/" ~ domain_path ~ "/" ~ this.identifier ~ "'"
    } -%}
    {%- if partitioning is not none -%}
        {%- do properties.update({'partitioning': "ARRAY['" ~ partitioning ~ "']"}) -%}
    {%- endif -%}
    {{ return(properties) }}
{%- endmacro %}
