"""Normalize one certified SSI REST capture into provider-neutral market tables."""

from pyspark.sql import SparkSession

from emr_jobs.common.iceberg import qualified_name
from emr_jobs.market_data.manifest import CaptureRun
from lakehouse.contracts.curated import CuratedProductContract


def _capture_view(
    spark: SparkSession,
    *,
    landing_table: str,
    capture: CaptureRun,
) -> None:
    request_ids = spark.createDataFrame(
        [(item.request_id,) for item in capture.requests], "request_id string"
    )
    request_ids.createOrReplaceTempView("ssi_capture_request_ids")
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_capture_records AS
        SELECT records.*
        FROM {landing_table} records
        JOIN ssi_capture_request_ids requests USING (request_id)
        """
    )


def _symbols(spark: SparkSession, endpoint: str) -> set[str]:
    return {
        row["symbol"]
        for row in spark.sql(
            f"""
            SELECT DISTINCT symbol
            FROM ssi_capture_records
            WHERE endpoint = '{endpoint}' AND symbol IS NOT NULL
            """
        ).collect()
    }


def _validate_scope(spark: SparkSession, capture: CaptureRun) -> bool:
    expected_symbols = set(capture.symbols)
    expected_indices = set(capture.indices)
    info = _symbols(spark, "get_securities_info")
    daily = _symbols(spark, "get_ohlc_1day_historical")
    minute = _symbols(spark, "get_ohlc_1minute_historical")
    master = _symbols(spark, "get_master_data_historical")
    indices = _symbols(spark, "get_index_summary_historical")
    if info != expected_symbols:
        raise RuntimeError(f"SSI security scope mismatch: expected={len(expected_symbols)}")
    has_market_data = bool(daily or minute or master or indices)
    if not has_market_data:
        return False
    if daily != expected_symbols or minute != expected_symbols or master != expected_symbols:
        raise RuntimeError("SSI completed-day stock scope is incomplete")
    if indices != expected_indices:
        raise RuntimeError("SSI completed-day index scope is incomplete")
    return True


def _publish_securities(
    spark: SparkSession,
    *,
    target: str,
) -> None:
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW ssi_security_candidates AS
        SELECT
            symbol,
            effective_from,
            effective_to,
            exchange,
            board,
            security_name,
            security_name_en,
            board_lot,
            listed_shares,
            icb_code,
            first_trading_date,
            last_trading_date,
            is_active,
            available_at,
            processed_at,
            source_record_sha256
        FROM (
            SELECT
                symbol,
                to_date(from_utc_timestamp(received_at, 'Asia/Ho_Chi_Minh'))
                    AS effective_from,
                CAST(NULL AS date) AS effective_to,
                upper(get_json_object(record_json, '$.board')) AS exchange,
                upper(get_json_object(record_json, '$.board')) AS board,
                nullif(trim(get_json_object(record_json, '$.symbol_name_vi')), '')
                    AS security_name,
                nullif(trim(get_json_object(record_json, '$.symbol_name_en')), '')
                    AS security_name_en,
                try_cast(get_json_object(record_json, '$.lot_size') AS bigint) AS board_lot,
                try_cast(get_json_object(record_json, '$.listed_shares') AS bigint)
                    AS listed_shares,
                nullif(trim(get_json_object(record_json, '$.icb_code')), '') AS icb_code,
                to_date(get_json_object(record_json, '$.first_trading_date'), 'yyyy/MM/dd')
                    AS first_trading_date,
                to_date(get_json_object(record_json, '$.last_trading_date'), 'yyyy/MM/dd')
                    AS last_trading_date,
                coalesce(
                    to_date(get_json_object(record_json, '$.last_trading_date'), 'yyyy/MM/dd')
                        >= to_date(from_utc_timestamp(received_at, 'Asia/Ho_Chi_Minh')),
                    true
                ) AS is_active,
                received_at AS available_at,
                current_timestamp() AS processed_at,
                record_sha256 AS source_record_sha256,
                row_number() OVER (
                    PARTITION BY symbol ORDER BY received_at DESC, record_sha256 DESC
                ) AS _rank
            FROM ssi_capture_records
            WHERE endpoint = 'get_securities_info'
        ) ranked
        WHERE _rank = 1
        """
    )
    invalid = spark.sql(
        """
        SELECT 1 FROM ssi_security_candidates
        WHERE symbol IS NULL OR exchange IS NULL OR available_at IS NULL
        LIMIT 1
        """
    ).count()
    if invalid:
        raise RuntimeError("SSI security normalization produced invalid required fields")
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_security_changes AS
        SELECT candidate.*
        FROM ssi_security_candidates candidate
        LEFT JOIN {target} current
          ON current.symbol = candidate.symbol AND current.effective_to IS NULL
        WHERE current.symbol IS NULL OR NOT (
            current.exchange <=> candidate.exchange
            AND current.board <=> candidate.board
            AND current.security_name <=> candidate.security_name
            AND current.security_name_en <=> candidate.security_name_en
            AND current.board_lot <=> candidate.board_lot
            AND current.listed_shares <=> candidate.listed_shares
            AND current.icb_code <=> candidate.icb_code
            AND current.first_trading_date <=> candidate.first_trading_date
            AND current.last_trading_date <=> candidate.last_trading_date
            AND current.is_active <=> candidate.is_active
        )
        """
    )
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING ssi_security_changes source
        ON target.symbol = source.symbol
           AND target.effective_to IS NULL
           AND target.effective_from < source.effective_from
        WHEN MATCHED THEN UPDATE SET effective_to = date_sub(source.effective_from, 1)
        """
    )
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING ssi_security_changes source
        ON target.symbol = source.symbol AND target.effective_from = source.effective_from
        WHEN MATCHED AND source.available_at > target.available_at THEN UPDATE SET
            effective_to = source.effective_to,
            exchange = source.exchange,
            board = source.board,
            security_name = source.security_name,
            security_name_en = source.security_name_en,
            board_lot = source.board_lot,
            listed_shares = source.listed_shares,
            icb_code = source.icb_code,
            first_trading_date = source.first_trading_date,
            last_trading_date = source.last_trading_date,
            is_active = source.is_active,
            available_at = source.available_at,
            processed_at = source.processed_at,
            source_record_sha256 = source.source_record_sha256
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def _market_views(spark: SparkSession, source_date: str) -> None:
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_daily_ohlc AS
        SELECT
            symbol,
            to_date(substr(get_json_object(record_json, '$.trading_date'), 1, 10),
                'yyyy/MM/dd') AS trade_date,
            try_cast(get_json_object(record_json, '$.open_price') AS decimal(18, 0))
                AS open_price,
            try_cast(get_json_object(record_json, '$.high_price') AS decimal(18, 0))
                AS high_price,
            try_cast(get_json_object(record_json, '$.low_price') AS decimal(18, 0))
                AS low_price,
            try_cast(get_json_object(record_json, '$.close_price') AS decimal(18, 0))
                AS close_price,
            try_cast(get_json_object(record_json, '$.volume') AS bigint) AS volume,
            try_cast(get_json_object(record_json, '$.value') AS decimal(24, 0)) AS value,
            received_at,
            record_sha256
        FROM ssi_capture_records
        WHERE endpoint = 'get_ohlc_1day_historical'
          AND substr(get_json_object(record_json, '$.trading_date'), 1, 10)
              = replace('{source_date}', '-', '/')
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_minute_ohlc AS
        SELECT
            symbol,
            trade_date,
            bar_start,
            coalesce(nullif(open_price, 0), nullif(close_price, 0)) AS open_price,
            coalesce(nullif(high_price, 0), nullif(close_price, 0)) AS high_price,
            coalesce(nullif(low_price, 0), nullif(close_price, 0)) AS low_price,
            nullif(close_price, 0) AS close_price,
            volume,
            value,
            received_at,
            record_sha256
        FROM (
            SELECT
                symbol,
                DATE '{source_date}' AS trade_date,
                to_utc_timestamp(
                    to_timestamp(get_json_object(record_json, '$.trading_date'),
                        'yyyy/MM/dd HH:mm:ss'),
                    'Asia/Ho_Chi_Minh'
                ) AS bar_start,
                try_cast(get_json_object(record_json, '$.open_price') AS decimal(18, 0))
                    AS open_price,
                try_cast(get_json_object(record_json, '$.high_price') AS decimal(18, 0))
                    AS high_price,
                try_cast(get_json_object(record_json, '$.low_price') AS decimal(18, 0))
                    AS low_price,
                try_cast(get_json_object(record_json, '$.close_price') AS decimal(18, 0))
                    AS close_price,
                try_cast(get_json_object(record_json, '$.volume') AS bigint) AS volume,
                try_cast(get_json_object(record_json, '$.value') AS decimal(24, 0)) AS value,
                received_at,
                record_sha256
            FROM ssi_capture_records
            WHERE endpoint = 'get_ohlc_1minute_historical'
              AND substr(get_json_object(record_json, '$.trading_date'), 1, 10)
                  = replace('{source_date}', '-', '/')
        ) parsed
        WHERE NOT (
            coalesce(open_price, 0) = 0
            AND coalesce(high_price, 0) = 0
            AND coalesce(low_price, 0) = 0
            AND coalesce(close_price, 0) = 0
            AND coalesce(volume, 0) = 0
            AND coalesce(value, 0) = 0
        )
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_security_summary AS
        SELECT
            symbol,
            to_date(substr(get_json_object(record_json, '$.trading_date'), 1, 10),
                'yyyy/MM/dd') AS trade_date,
            try_cast(get_json_object(record_json, '$.open_price') AS decimal(18, 0))
                AS open_price,
            try_cast(get_json_object(record_json, '$.high_price') AS decimal(18, 0))
                AS high_price,
            try_cast(get_json_object(record_json, '$.low_price') AS decimal(18, 0))
                AS low_price,
            try_cast(get_json_object(record_json, '$.close_price') AS decimal(18, 0))
                AS close_price,
            try_cast(get_json_object(record_json, '$.total_match') AS bigint)
                AS matched_volume,
            try_cast(get_json_object(record_json, '$.total_match_value') AS decimal(24, 0))
                AS matched_value,
            received_at,
            record_sha256
        FROM ssi_capture_records
        WHERE endpoint = 'get_securities_summary_historical'
          AND substr(get_json_object(record_json, '$.trading_date'), 1, 10)
              = replace('{source_date}', '-', '/')
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_master_data AS
        SELECT
            symbol,
            to_date(substr(get_json_object(record_json, '$.trading_date'), 1, 10),
                'yyyy/MM/dd') AS trade_date,
            try_cast(get_json_object(record_json, '$.ref_price') AS decimal(18, 0))
                AS reference_price,
            try_cast(get_json_object(record_json, '$.ceiling') AS decimal(18, 0))
                AS ceiling_price,
            try_cast(get_json_object(record_json, '$.floor') AS decimal(18, 0))
                AS floor_price,
            received_at,
            record_sha256
        FROM ssi_capture_records
        WHERE endpoint = 'get_master_data_historical'
          AND substr(get_json_object(record_json, '$.trading_date'), 1, 10)
              = replace('{source_date}', '-', '/')
        """
    )


def _validate_market_data(spark: SparkSession, capture: CaptureRun) -> None:
    coverage = spark.sql(
        f"""
        SELECT 1
        WHERE (SELECT count(*) FROM ssi_daily_ohlc) != {len(capture.symbols)}
           OR (SELECT count(DISTINCT symbol) FROM ssi_daily_ohlc)
                != {len(capture.symbols)}
           OR (SELECT count(*) FROM ssi_master_data) != {len(capture.symbols)}
           OR (SELECT count(DISTINCT symbol) FROM ssi_master_data)
                != {len(capture.symbols)}
           OR (SELECT count(*) FROM ssi_security_summary) != {len(capture.symbols)}
           OR (SELECT count(DISTINCT symbol) FROM ssi_security_summary)
                != {len(capture.symbols)}
           OR (SELECT count(DISTINCT symbol) FROM ssi_minute_ohlc)
                != {len(capture.symbols)}
           OR EXISTS (
                SELECT 1 FROM ssi_minute_ohlc
                GROUP BY symbol, bar_start HAVING count(*) > 1
           )
        """
    ).count()
    if coverage:
        raise RuntimeError("SSI completed-day records are missing or duplicated")

    invalid = spark.sql(
        """
        SELECT 1 FROM (
            SELECT symbol, trade_date, CAST(NULL AS timestamp) AS bar_start, false AS is_minute,
                open_price, high_price, low_price, close_price, volume, value
            FROM ssi_daily_ohlc
            UNION ALL
            SELECT symbol, trade_date, bar_start, true AS is_minute,
                open_price, high_price, low_price, close_price, volume, value
            FROM ssi_minute_ohlc
        ) ohlc
        WHERE symbol IS NULL OR trade_date IS NULL
           OR (is_minute AND bar_start IS NULL)
           OR open_price IS NULL OR high_price IS NULL
           OR low_price IS NULL OR close_price IS NULL OR volume IS NULL OR volume < 0
           OR open_price <= 0 OR high_price <= 0 OR low_price <= 0 OR close_price <= 0
           OR value < 0
           OR high_price < greatest(open_price, low_price, close_price)
           OR low_price > least(open_price, high_price, close_price)
        LIMIT 1
        """
    ).count()
    if invalid:
        raise RuntimeError("SSI OHLC normalization produced invalid values")
    invalid_reference = spark.sql(
        """
        SELECT 1 FROM ssi_master_data
        WHERE symbol IS NULL OR trade_date IS NULL
           OR reference_price IS NULL OR reference_price <= 0
           OR ceiling_price IS NULL OR ceiling_price < reference_price
           OR floor_price IS NULL OR floor_price > reference_price OR floor_price <= 0
        LIMIT 1
        """
    ).count()
    if invalid_reference:
        raise RuntimeError("SSI reference-price normalization produced invalid values")
    invalid_summary = spark.sql(
        """
        SELECT 1 FROM ssi_security_summary
        WHERE symbol IS NULL OR trade_date IS NULL
           OR open_price IS NULL OR high_price IS NULL
           OR low_price IS NULL OR close_price IS NULL
           OR matched_volume IS NULL OR matched_volume < 0
           OR matched_value IS NULL OR matched_value < 0
        LIMIT 1
        """
    ).count()
    if invalid_summary:
        raise RuntimeError("SSI security-summary normalization produced invalid values")
    summary_mismatch = spark.sql(
        """
        SELECT 1
        FROM ssi_daily_ohlc daily
        JOIN ssi_security_summary summary USING (symbol, trade_date)
        WHERE daily.open_price != summary.open_price
           OR daily.high_price != summary.high_price
           OR daily.low_price != summary.low_price
           OR daily.close_price != summary.close_price
           OR daily.volume != summary.matched_volume
        LIMIT 1
        """
    ).count()
    if summary_mismatch:
        raise RuntimeError("SSI daily OHLC and security-summary records do not reconcile")
    mismatch = spark.sql(
        """
        SELECT 1
        FROM ssi_daily_ohlc daily
        JOIN (
            SELECT
                symbol,
                min_by(open_price, bar_start) AS open_price,
                max(high_price) AS high_price,
                min(low_price) AS low_price,
                max_by(close_price, bar_start) AS close_price,
                sum(volume) AS volume
            FROM ssi_minute_ohlc
            GROUP BY symbol
        ) minute USING (symbol)
        WHERE daily.open_price != minute.open_price
           OR daily.high_price != minute.high_price
           OR daily.low_price != minute.low_price
           OR daily.close_price != minute.close_price
           OR daily.volume != minute.volume
        LIMIT 1
        """
    ).count()
    if mismatch:
        raise RuntimeError("SSI daily and one-minute OHLC records do not reconcile")


def _publish_daily(spark: SparkSession, target: str) -> None:
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW ssi_daily_candidates AS
        SELECT
            daily.symbol,
            daily.trade_date,
            'ssi_rest_daily_reconciled' AS source_kind,
            daily.open_price,
            daily.high_price,
            daily.low_price,
            daily.close_price,
            master.reference_price,
            master.ceiling_price,
            master.floor_price,
            summary.matched_volume,
            summary.matched_value,
            CAST(NULL AS bigint) AS deal_volume,
            CAST(NULL AS decimal(24, 0)) AS deal_value,
            CAST(NULL AS bigint) AS foreign_buy_volume,
            CAST(NULL AS bigint) AS foreign_sell_volume,
            CAST(NULL AS decimal(24, 0)) AS foreign_buy_value,
            CAST(NULL AS decimal(24, 0)) AS foreign_sell_value,
            greatest(daily.received_at, master.received_at, summary.received_at)
                AS available_at,
            current_timestamp() AS processed_at,
            true AS is_final,
            sha2(concat_ws('|', daily.record_sha256, master.record_sha256,
                coalesce(summary.record_sha256, '')), 256) AS source_record_sha256
        FROM ssi_daily_ohlc daily
        JOIN ssi_master_data master USING (symbol, trade_date)
        JOIN ssi_security_summary summary USING (symbol, trade_date)
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_daily_new AS
        SELECT
            candidate.symbol,
            candidate.trade_date,
            coalesce(revisions.max_revision + 1, 0) AS revision,
            candidate.source_kind,
            candidate.open_price,
            candidate.high_price,
            candidate.low_price,
            candidate.close_price,
            candidate.reference_price,
            candidate.ceiling_price,
            candidate.floor_price,
            candidate.matched_volume,
            candidate.matched_value,
            candidate.deal_volume,
            candidate.deal_value,
            candidate.foreign_buy_volume,
            candidate.foreign_sell_volume,
            candidate.foreign_buy_value,
            candidate.foreign_sell_value,
            candidate.available_at,
            candidate.processed_at,
            candidate.is_final,
            candidate.source_record_sha256
        FROM ssi_daily_candidates candidate
        LEFT JOIN (
            SELECT symbol, trade_date, max(revision) AS max_revision
            FROM {target} GROUP BY symbol, trade_date
        ) revisions USING (symbol, trade_date)
        LEFT ANTI JOIN {target} existing
          ON existing.symbol = candidate.symbol
         AND existing.trade_date = candidate.trade_date
         AND existing.source_record_sha256 = candidate.source_record_sha256
        """
    )
    spark.sql(f"INSERT INTO {target} SELECT * FROM ssi_daily_new")


def _publish_bars(spark: SparkSession, target: str) -> None:
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW ssi_bar_candidates AS
        SELECT
            symbol,
            trade_date,
            bar_start,
            'ssi_rest_1m_historical' AS source_kind,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            value,
            CAST(NULL AS bigint) AS trade_count,
            CAST(NULL AS decimal(24, 8)) AS vwap,
            CAST(NULL AS bigint) AS buy_volume,
            CAST(NULL AS bigint) AS sell_volume,
            received_at AS available_at,
            current_timestamp() AS processed_at,
            true AS is_final,
            record_sha256 AS source_record_sha256
        FROM ssi_minute_ohlc
        """
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_bars_new AS
        SELECT
            candidate.symbol,
            candidate.trade_date,
            candidate.bar_start,
            coalesce(revisions.max_revision + 1, 0) AS revision,
            candidate.source_kind,
            candidate.open_price,
            candidate.high_price,
            candidate.low_price,
            candidate.close_price,
            candidate.volume,
            candidate.value,
            candidate.trade_count,
            candidate.vwap,
            candidate.buy_volume,
            candidate.sell_volume,
            candidate.available_at,
            candidate.processed_at,
            candidate.is_final,
            candidate.source_record_sha256
        FROM ssi_bar_candidates candidate
        LEFT JOIN (
            SELECT symbol, bar_start, max(revision) AS max_revision
            FROM {target} GROUP BY symbol, bar_start
        ) revisions USING (symbol, bar_start)
        LEFT ANTI JOIN {target} existing
          ON existing.symbol = candidate.symbol
         AND existing.bar_start = candidate.bar_start
         AND existing.source_record_sha256 = candidate.source_record_sha256
        """
    )
    spark.sql(f"INSERT INTO {target} SELECT * FROM ssi_bars_new")


def _publish_indices(
    spark: SparkSession,
    target: str,
    source_date: str,
    expected_indices: int,
) -> None:
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW ssi_index_candidates AS
        SELECT
            sha2(concat_ws('|', symbol, '{source_date}', record_sha256), 256)
                AS index_snapshot_id,
            symbol AS index_code,
            DATE '{source_date}' AS trade_date,
            CAST(NULL AS timestamp) AS event_time,
            'ssi_rest_index_summary_historical' AS source_kind,
            try_cast(get_json_object(record_json, '$.index_value') AS decimal(18, 6))
                AS index_value,
            try_cast(get_json_object(record_json, '$.index_change') AS decimal(18, 6))
                AS point_change,
            try_cast(get_json_object(record_json, '$.index_change_percent') AS decimal(18, 8))
                AS percent_change,
            try_cast(get_json_object(record_json, '$.total_advance_stock') AS bigint)
                AS advancing_count,
            try_cast(get_json_object(record_json, '$.total_decline_stock') AS bigint)
                AS declining_count,
            try_cast(get_json_object(record_json, '$.total_steady_stock') AS bigint)
                AS unchanged_count,
            try_cast(get_json_object(record_json, '$.total_ceiling_stock') AS bigint)
                AS ceiling_count,
            try_cast(get_json_object(record_json, '$.total_floor_stock') AS bigint)
                AS floor_count,
            try_cast(get_json_object(record_json, '$.total_match') AS bigint)
                AS matched_volume,
            try_cast(get_json_object(record_json, '$.total_match_value') AS decimal(24, 0))
                AS matched_value,
            received_at AS available_at,
            current_timestamp() AS processed_at,
            record_sha256 AS source_record_sha256
        FROM ssi_capture_records
        WHERE endpoint = 'get_index_summary_historical'
          AND substr(get_json_object(record_json, '$.trading_date'), 1, 10)
              = replace('{source_date}', '-', '/')
        """
    )
    invalid = spark.sql(
        f"""
        SELECT 1 FROM ssi_index_candidates
        WHERE index_snapshot_id IS NULL OR index_code IS NULL
           OR index_value IS NULL OR index_value <= 0
           OR advancing_count < 0 OR declining_count < 0 OR unchanged_count < 0
           OR ceiling_count < 0 OR floor_count < 0
           OR matched_volume < 0 OR matched_value < 0
        UNION ALL
        SELECT 1 FROM (
            SELECT count(*) AS records, count(DISTINCT index_code) AS indices
            FROM ssi_index_candidates
        ) coverage
        WHERE records != {expected_indices} OR indices != {expected_indices}
        LIMIT 1
        """
    ).count()
    if invalid:
        raise RuntimeError("SSI index normalization produced invalid required fields")
    spark.sql(
        f"""
        MERGE INTO {target} target
        USING ssi_index_candidates source
        ON target.index_snapshot_id = source.index_snapshot_id
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def publish(
    spark: SparkSession,
    *,
    landing_table: str,
    product: CuratedProductContract,
    capture: CaptureRun,
    source_date: str,
) -> bool:
    """Publish current REST-supported tables; return whether market facts were present."""
    _capture_view(spark, landing_table=landing_table, capture=capture)
    has_market_data = _validate_scope(spark, capture)
    securities = qualified_name(product.table_identifier("securities"))
    _publish_securities(spark, target=securities)
    if not has_market_data:
        return False

    _market_views(spark, source_date)
    _validate_market_data(spark, capture)
    _publish_daily(
        spark,
        qualified_name(product.table_identifier("daily_security_summaries")),
    )
    _publish_bars(
        spark,
        qualified_name(product.table_identifier("intraday_bars_1m")),
    )
    _publish_indices(
        spark,
        qualified_name(product.table_identifier("index_snapshots")),
        source_date,
        len(capture.indices),
    )
    return True
