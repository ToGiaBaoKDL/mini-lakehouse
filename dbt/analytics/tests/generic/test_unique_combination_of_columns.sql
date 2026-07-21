{% test unique_combination_of_columns(model, combination_of_columns) %}

select
    {% for column_name in combination_of_columns %}
        {{ column_name }}{% if not loop.last %},{% endif %}
    {% endfor %},
    count(*) as duplicate_count
from {{ model }}
group by
    {% for column_name in combination_of_columns %}
        {{ column_name }}{% if not loop.last %},{% endif %}
    {% endfor %}
having count(*) > 1

{% endtest %}
