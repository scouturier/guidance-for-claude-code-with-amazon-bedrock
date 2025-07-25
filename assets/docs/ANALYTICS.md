# Claude Code Analytics Pipeline

This directory contains the CloudFormation templates for setting up an analytics pipeline to track Claude Code usage metrics.

## Overview

The analytics pipeline consists of:

- **Kinesis Data Firehose**: Streams CloudWatch Logs to S3 in Parquet format
- **S3 Data Lake**: Stores historical metrics data with automatic archival
- **AWS Athena**: Enables SQL queries on the metrics data
- **Partition Projection**: Eliminates the need for Glue crawlers

![Overview](../images/otel-monitoring-flow.png)

## Deployment

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. Claude Code OTEL collector already deployed and sending metrics to CloudWatch Logs

### Deploy the Analytics Pipeline

```bash
# Deploy the analytics pipeline
aws cloudformation deploy \
  --template-file analytics-pipeline.yaml \
  --stack-name claude-code-analytics \
  --capabilities CAPABILITY_IAM

# Get the Athena console URL
aws cloudformation describe-stacks \
  --stack-name claude-code-analytics \
  --query 'Stacks[0].Outputs[?OutputKey==`AthenaConsoleUrl`].OutputValue' \
  --output text
```

### Update the Monitoring Dashboard

```bash
# Update the dashboard to remove hard-coded users
aws cloudformation deploy \
  --template-file monitoring-dashboard.yaml \
  --stack-name claude-code-auth-dashboard \
  --parameter-overrides TokenCostPerMillion=15.0
```

## Using Athena for User Analytics

### Access Athena Console

1. Navigate to the Athena console URL provided in the stack outputs
2. Select the workgroup created by the stack (e.g., `claude-code-analytics-workgroup`)
3. Select the database (e.g., `claude_code_analytics_analytics`)

### Sample Queries

The stack creates several named queries that you can use as starting points:

#### Top Users by Token Usage (Last 7 Days)

```sql
WITH user_totals AS (
    SELECT
        user_id,
        SUM(token_usage) as total_tokens,
        COUNT(DISTINCT session_id) as session_count,
        COUNT(DISTINCT DATE(from_unixtime(timestamp/1000))) as active_days
    FROM metrics
    WHERE year >= YEAR(CURRENT_DATE - INTERVAL '7' DAY)
        AND from_unixtime(timestamp/1000) >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
    GROUP BY user_id
)
SELECT
    SUBSTR(user_id, 1, 8) || '...' as user_id_short,
    total_tokens,
    session_count,
    active_days,
    ROUND(total_tokens * 0.000015, 2) as estimated_cost_usd
FROM user_totals
ORDER BY total_tokens DESC
LIMIT 10;
```

#### Token Usage by Model

```sql
SELECT
    model,
    type as token_type,
    SUM(token_usage) as total_tokens,
    COUNT(DISTINCT user_id) as unique_users,
    ROUND(SUM(token_usage) * 0.000015, 2) as estimated_cost_usd
FROM metrics
WHERE year >= YEAR(CURRENT_DATE - INTERVAL '30' DAY)
    AND from_unixtime(timestamp/1000) >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY model, type
ORDER BY total_tokens DESC;
```

#### User Activity by Hour

```sql
SELECT
    HOUR(from_unixtime(timestamp/1000)) as hour_of_day,
    COUNT(DISTINCT user_id) as active_users,
    SUM(token_usage) as total_tokens
FROM metrics
WHERE year >= YEAR(CURRENT_DATE - INTERVAL '7' DAY)
    AND from_unixtime(timestamp/1000) >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY HOUR(from_unixtime(timestamp/1000))
ORDER BY hour_of_day;
```

### Custom Time Ranges

To query different time ranges, modify the WHERE clause:

```sql
-- Last 24 hours
WHERE from_unixtime(timestamp/1000) >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR

-- Last 30 days
WHERE year >= YEAR(CURRENT_DATE - INTERVAL '30' DAY)
    AND from_unixtime(timestamp/1000) >= CURRENT_TIMESTAMP - INTERVAL '30' DAY

-- Specific date range
WHERE from_unixtime(timestamp/1000) BETWEEN TIMESTAMP '2024-01-01' AND TIMESTAMP '2024-01-31'
```

## Data Retention

- **S3 Standard**: 90 days (configurable via `DataRetentionDays` parameter)
- **S3 Glacier**: After 90 days (automatic transition)
- **Athena Query Results**: 7 days (auto-deleted)

## Cost Optimization

1. **Partition Projection**: No need to run Glue crawlers
2. **Parquet Format**: Columnar storage reduces query costs
3. **S3 Lifecycle**: Automatic archival to Glacier
4. **Query Result Caching**: Athena caches results for 7 days

### Query Performance

- Use partition columns (year, month, day, hour) in WHERE clauses
- Limit time ranges to reduce data scanned
- Use LIMIT for exploratory queries
