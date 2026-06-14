function Update()
  local condMeasure = SKIN:GetMeasure('MeasureWeatherEmojiIndex')
  if condMeasure == nil then return 'DAY' end
  local cond = condMeasure:GetStringValue()
  cond = extract_weather_keyword(cond)
  -- SKIN:Bang('!Log', 'Weather condition: ' .. cond)
  if cond == 'DAYNIGHT' then
    local isNight = SKIN:GetMeasure('MeasureIsNight'):GetValue()
    if isNight == 1 then return 'NIGHT' end
    return 'DAY'
  end
  return cond
end

function extract_weather_keyword(s)
  local keywords = {"THUNDER","RAIN","SNOW","ICE","FOG","CLOUD","PARTLY","DAY","NIGHT"}
  for _, kw in ipairs(keywords) do
    if s:find(kw, 1, true) then
      return kw
    end
  end
  return s -- no match, return original
end

-- -- Examples:
-- print(extract_weather_keyword("PARTLY_CLOUDY_DAY"))  -- "PARTLY"
-- print(extract_weather_keyword("HEAVY_RAIN_shower"))  -- "RAIN"
-- print(extract_weather_keyword("clear"))              -- "clear"