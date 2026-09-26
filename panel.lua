-- Turn Panel: tracks round / active player / phase and tells the bridge
-- (tts_bridge.py listen) about every change via sendExternalMessage.
-- Left-click advances, right-click goes back.

local PHASES = {"Command", "Movement", "Shooting", "Charge", "Fight"}
local FIRST = "Red"

state = {round = 1, active = FIRST, phase = 1}

-- Buttons scale with the object; undo the panel's 6 x 0.3 x 4 scale.
local UNSCALE = {1 / 6, 1 / 0.3, 1 / 4}
local TOP = 0.51

local function other(color)
    return color == "Red" and "Blue" or "Red"
end

local function statusText()
    return string.format("Round %d  ·  %s\n%s phase", state.round, state.active, PHASES[state.phase])
end

local function emit(event, player)
    sendExternalMessage({
        event = event,
        player = player,
        round = state.round,
        active = state.active,
        phase = PHASES[state.phase],
    })
end

local function button(fn, label, x, z, w, h, font, bg)
    self.createButton({
        click_function = fn,
        function_owner = self,
        label = label,
        position = {x, TOP, z},
        scale = UNSCALE,
        width = w,
        height = h,
        font_size = font,
        color = bg or {0.25, 0.25, 0.3},
        font_color = {1, 1, 1},
        tooltip = fn == "noop" and "" or "Right-click to undo",
    })
end

function draw()
    self.clearButtons()
    local tint = state.active == "Red" and {0.55, 0.12, 0.12} or {0.12, 0.25, 0.6}
    button("noop", statusText(), 0, -0.25, 2600, 700, 280, tint)
    button("nextPhase", "Next Phase", -0.23, 0.12, 1250, 450, 220)
    button("passTurn", "Pass Turn", 0.23, 0.12, 1250, 450, 220)
    button("aiTurn", "AI: Take Turn", 0, 0.37, 2600, 400, 220, {0.2, 0.4, 0.25})
end

local function announce(player)
    broadcastToAll(string.format("[%s] Round %d, %s: %s phase",
        player or "?", state.round, state.active, PHASES[state.phase]), state.active)
end

function noop() end

function nextPhase(_, player, alt)
    if alt then
        if state.phase > 1 then
            state.phase = state.phase - 1
        end
    elseif state.phase < #PHASES then
        state.phase = state.phase + 1
    else
        return passTurn(_, player, false)
    end
    draw()
    announce(player)
    emit(alt and "prev_phase" or "next_phase", player)
end

function passTurn(_, player, alt)
    if alt then
        if state.active == FIRST then
            if state.round == 1 then return end
            state.round = state.round - 1
        end
        state.active = other(state.active)
        state.phase = 1
    else
        state.active = other(state.active)
        if state.active == FIRST then
            state.round = state.round + 1
        end
        state.phase = 1
    end
    draw()
    announce(player)
    emit(alt and "undo_pass" or "pass_turn", player)
end

function aiTurn(_, player)
    broadcastToAll("[" .. player .. "] asked the AI to take its turn", "Yellow")
    emit("ai_turn", player)
end

function onSave()
    return JSON.encode(state)
end

function onLoad(saved)
    if saved and saved ~= "" then
        state = JSON.decode(saved)
    end
    draw()
end
