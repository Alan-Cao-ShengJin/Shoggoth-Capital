"""Find x and + markers in candles listed oldest first.

Each candle is a dictionary: o=open, h=high, l=low, c=close, v=volume.
The high must be at least the open/close, and the low no greater than them.
Our data downloader checks these basic price relationships.
All candles except the last must be finished. The last is ignored by default;
pass last_bar_closed=True only when it has finished too.
calculate_signals(candles) returns one answer for each candle:
    -1 = bearish x, +1 = bullish +, 0 = leave the candle unmarked.

An absorption candle has little price movement but unusually high volume.
A zone is the price area around that candle. We track one zone at a time
so another absorption candle inside the same zone does not get a new marker.

Setup rules adapted from AlanCaoShengJin's Volume Efficiency (MPL-2.0).
"""

# CHANGE THESE SETTINGS. The calculations below use these values directly.
# What counts as small price movement and unusually high trading volume?
LOOKBACK = 100                   # Compare with the previous 100 candles.
SMALL_MOVEMENT_PERCENTILE = 30   # Movement in the bottom 30% qualifies.
HIGH_VOLUME_PERCENTILE = 70      # Volume in the top 30% qualifies.
MOVEMENT_MODE = "Body"           # "Body": open to close. "Full range": low to high.

# Which way was price trending before the absorption candle?
TREND_EMA_LENGTH = 20            # Smooth price using a 20-period moving average.
TREND_SLOPE_BARS = 3             # Check whether that average rose or fell over 3 bars.
ENABLE_BEARISH = True            # Allow x markers after an upward trend.
ENABLE_BULLISH = True            # Allow + markers after a downward trend.

# When can we stop tracking an old zone and allow a new marker?
ZONE_LIFETIME = 12               # Stop after 12 candles from the first setup.
MERGE_GAP = 3                    # Nearby absorption candles can join the same zone.
MERGE_WIDTH_ATR = 2.0            # Allow widening up to twice the usual price range.
ATR_LENGTH = 14                 # Use 14 candles to estimate the usual price range.
PIVOT_STRENGTH = 3              # Confirm a turning point with 3 candles on each side.
MAX_SWING_AGE = 100              # Ignore turning points more than 100 candles old.
STRUCTURE_BUFFER_ATR = 0.1      # Extra distance beyond the turning point; keep >= 0.

from math import fsum, isfinite


def valid_number(value):
    # Missing values and infinite numbers cannot describe a usable candle.
    return value is not None and isfinite(value)


def percentile_rank(values, index):
    # Example: 70 means 70% of earlier values were no bigger than today's value.
    # The current candle is compared ONLY with earlier candles, never future ones.
    count = 0
    for previous in values[index - LOOKBACK:index]:
        if previous <= values[index]:
            count += 1
    return 100 * count / LOOKBACK


def trend_ema(closes):
    # EMA is a smoothed price line. Recent prices get more weight than old ones.
    # Start at the first close, then move the average a little toward each new close.
    result = []
    average = None
    weight = 2 / (TREND_EMA_LENGTH + 1)
    for close in closes:
        if valid_number(close):
            if average is None:
                average = close
            else:
                average = weight * close + (1 - weight) * average
        result.append(average)
    return result


def average_true_range(bars):
    # ATR estimates how far price usually moves. Overnight gaps count as movement.
    # Start with a simple average, then smooth each new candle's range.
    result = []
    first_ranges = []
    average = None
    previous_close = None
    for bar in bars:
        high, low = bar["h"], bar["l"]
        if valid_number(high) and valid_number(low):
            true_range = high - low
            if valid_number(previous_close):
                true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
            if average is None:
                first_ranges.append(true_range)
                if len(first_ranges) == ATR_LENGTH:
                    average = fsum(first_ranges) / ATR_LENGTH
            else:
                average = true_range / ATR_LENGTH + (1 - 1 / ATR_LENGTH) * average
        result.append(average)
        previous_close = bar["c"]
    return result


def confirmed_swing(values, index, find_high):
    # A swing is a local peak or valley. Wait for candles on BOTH sides to prove it.
    # The right-hand candles stop at index: nothing beyond the current close is read.
    # This does not put a marker back on the earlier turning-point candle.
    # Return its price and candle number, or None if no turning point is confirmed.
    if index < 2 * PIVOT_STRENGTH:
        return None
    swing_index = index - PIVOT_STRENGTH
    window = values[swing_index - PIVOT_STRENGTH:index + 1]
    if not all(valid_number(value) for value in window):
        return None
    price = values[swing_index]
    left = window[:PIVOT_STRENGTH]
    right = window[PIVOT_STRENGTH + 1:]
    # If two peaks/valleys have the same price, use the later one.
    if find_high:
        qualifies = price >= max(left) and price > max(right)
    else:
        qualifies = price <= min(left) and price < min(right)
    if qualifies:
        return price, swing_index
    return None


def update_zone(zone, bar, index, absorption):
    # This function tracks an EXISTING zone. It never creates a marker.
    # Returning None means the old zone is finished.
    # Expiry ends the old zone immediately. There is no need to merge or test it.
    if index - zone["start"] >= ZONE_LIFETIME:
        return None
    if zone["side"] == -1:
        cancelled = bar["c"] > zone["high"]
        crossed = bar["c"] < zone["low"]
    else:
        cancelled = bar["c"] < zone["low"]
        crossed = bar["c"] > zone["high"]

    # Price moved the wrong way: stop tracking this zone.
    if cancelled:
        return None

    # Once price exits in the expected direction, freeze the zone's boundaries.
    # Until then, a nearby absorption candle can widen the same zone a little.
    if not zone["broken"]:
        if crossed:
            zone["broken"] = True
        elif absorption and index - zone["last_merge"] <= MERGE_GAP:
            high = max(zone["high"], bar["h"])
            low = min(zone["low"], bar["l"])
            allowed_width = max(zone["high"] - zone["low"], MERGE_WIDTH_ATR * zone["atr"])
            # Its close is still inside the zone, so a valid candle already overlaps it.
            if high - low <= allowed_width:
                zone["high"], zone["low"] = high, low
                zone["last_merge"] = index  # This does not reset the zone's start.

    # Without an old turning point, only expiry or cancellation can finish a zone.
    if zone["swing"] is None:
        return zone

    # This target is beyond BOTH the zone and the old turning point, plus a cushion.
    # Reaching it already proves price left the zone; no second exit check is needed.
    cushion = STRUCTURE_BUFFER_ATR * zone["atr"]
    if zone["side"] == -1:
        target = min(zone["swing"], zone["low"]) - cushion
        if bar["c"] < target:
            return None
    else:
        target = max(zone["swing"], zone["high"]) + cushion
        if bar["c"] > target:
            return None
    return zone


def calculate_signals(bars, *, last_bar_closed=False):
    # Lookback sizes must be positive. Negative offsets could point into the future.
    if min(LOOKBACK, TREND_EMA_LENGTH, TREND_SLOPE_BARS, ATR_LENGTH, PIVOT_STRENGTH) < 1:
        raise ValueError("Lookback, EMA, trend slope, ATR and pivot sizes must be at least 1.")

    # 0. An unfinished candle can change its prices and volume. Ignore it completely.
    #    Leave its answer at zero; use it only after its close has been confirmed.
    signals = [0] * len(bars)
    if not last_bar_closed:
        bars = bars[:-1]

    # 1. Put each kind of price into its own list so the rules can read it easily.
    closes = [bar["c"] for bar in bars]
    highs = [bar["h"] for bar in bars]
    lows = [bar["l"] for bar in bars]
    volumes = [bar["v"] for bar in bars]
    movements = []
    valid_bars = []

    # 2. Measure how much each candle moved. The body ignores the wicks;
    #    the full range includes them. Zero/missing volume makes a candle unusable.
    for bar in bars:
        valid_prices = all(valid_number(bar[key]) for key in ("o", "h", "l", "c"))
        movement = None
        if valid_prices:
            if MOVEMENT_MODE == "Body":
                movement = abs(bar["c"] - bar["o"])
            else:
                movement = bar["h"] - bar["l"]
        movements.append(movement)
        valid_bars.append(valid_prices and valid_number(bar["v"]) and bar["v"] > 0)

    emas = trend_ema(closes)
    atrs = average_true_range(bars)

    # Nothing is being watched at the beginning. None means "nothing yet".
    zone = None
    last_high = None
    last_low = None

    for i, bar in enumerate(bars):
        # 3. Wait for usable ranking history AND a typical price-range estimate.
        #    The second check also allows LOOKBACK to be shorter than ATR_LENGTH.
        ready = i >= LOOKBACK and atrs[i] is not None and all(valid_bars[i - LOOKBACK:i + 1])
        absorption = False
        if ready:
            movement_rank = percentile_rank(movements, i)
            volume_rank = percentile_rank(volumes, i)
            # 4. Absorption means BOTH small price movement and unusually high volume.
            absorption = movement_rank <= SMALL_MOVEMENT_PERCENTILE and volume_rank >= HIGH_VOLUME_PERCENTILE

        if not ready:
            zone = None

        elif zone is not None:
            # 5. Deal with the old zone first. Even if it finishes here, this candle
            #    cannot start a new zone; the next candle gets that opportunity.
            zone = update_zone(zone, bar, i, absorption)

        elif absorption and i >= TREND_SLOPE_BARS + 1:
            # 6. A new absorption candle gets x after an uptrend, or + after a
            #    downtrend. This candle's own colour does not choose the direction.
            previous_close = closes[i - 1]
            previous_ema = emas[i - 1]
            earlier_ema = emas[i - 1 - TREND_SLOPE_BARS]
            side = 0
            if ENABLE_BEARISH and previous_close > previous_ema and previous_ema > earlier_ema:
                side = -1
            elif ENABLE_BULLISH and previous_close < previous_ema and previous_ema < earlier_ema:
                side = 1

            if side != 0:
                signals[i] = side  # Mark this closed candle only. Never edit an older answer.
                # Remember the setup's price area and an older turning point.
                # This information decides when another marker becomes possible.
                reference = None
                swing = last_low if side == -1 else last_high
                if swing is not None and i - swing[1] <= MAX_SWING_AGE:
                    if side == -1 and bar["c"] > swing[0]:
                        reference = swing[0]
                    elif side == 1 and bar["c"] < swing[0]:
                        reference = swing[0]
                zone = {
                    "side": side, "start": i, "last_merge": i,
                    "high": bar["h"], "low": bar["l"], "atr": atrs[i],
                    "swing": reference, "broken": False,
                }

        # 7. Remember any newly confirmed turning points AFTER checking this
        #    candle. A new turning point can only affect later candles.
        high_swing = confirmed_swing(highs, i, find_high=True)
        low_swing = confirmed_swing(lows, i, find_high=False)
        if high_swing is not None:
            last_high = high_swing
        if low_swing is not None:
            last_low = low_swing

    return signals
