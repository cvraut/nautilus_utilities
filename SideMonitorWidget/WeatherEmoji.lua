function Update()
  local condMeasure = SKIN:GetMeasure('MeasureWeatherEmojiIndex')
  if condMeasure == nil then return 'DAY' end
  local cond = condMeasure:GetStringValue()
  if cond == 'DAYNIGHT' then
    local isNight = SKIN:GetMeasure('MeasureIsNight'):GetValue()
    if isNight == 1 then return 'NIGHT' end
    return 'DAY'
  end
  return cond
end