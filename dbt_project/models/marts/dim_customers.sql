with customers as (
    select * from {{ ref('customer_master') }}
)

select
    customer_id,
    first_name || ' ' || last_name as full_name,
    country_code,
    created_at
from customers
