// Package ops runs MAINTENANCE operations on the trading VDS — the OS and QUIK
// itself — on the operator's behalf.
//
// WHY. On 25.09.2026 the QUIK DDE export died at 12:09. The agent was alive, the
// link to STL was green, Lua answered pings — yet no table updated: the tape stood
// still for ten minutes, account data for eleven. Diagnosing that takes thirty
// seconds (are the tables open, is DDE export on?) and fixing it takes three clicks,
// but nobody could reach the machine: the operator was on the road, rebooted the VDS
// blind, and it did not help. Trading stood for over an hour.
//
// WHAT THIS IS NOT. Not a remote shell. There is no "run this command" here and
// never will be: `op` is an id from the catalog COMPILED INTO THIS BINARY, and an id
// that is not in it does not run, whoever sent it and however well signed. Adding an
// operation costs a code change, a review and an agent release — that is the price
// this channel pays for living next to real money.
//
// CONFIRMATION. Operations that change state carry a one-time `confirm_id` issued by
// a HUMAN (a button in the companion or an SMS reply) and bound to this operation AND
// these arguments: a token that approved restarting QUIK cannot reboot the machine.
// It expires in five minutes, because an approval given half an hour ago belongs to a
// different market situation.
package ops

import (
	"fmt"
	"sort"
	"strings"
)

// Risk classifies what an operation can cost if it fires at the wrong moment.
type Risk int

const (
	// RiskNone — reads only. No confirmation: these change nothing, and it was
	// precisely their absence that cost an hour of blind downtime on 25.09.
	RiskNone Risk = iota
	// RiskLow — changes the terminal's own state (opens a window, turns DDE export
	// back on) without interrupting the data feed.
	RiskLow
	// RiskMedium — restarts a process of ours (runner, agent).
	RiskMedium
	// RiskHigh — restarts QUIK, reinstalls it, reboots the machine. A QUIK restart
	// makes the tape REPLAY the day, and robots have already piled real orders on
	// such a replay; that is why these also demand the real robots be paused.
	RiskHigh
)

// Op is one catalogued operation.
type Op struct {
	ID   string
	Risk Risk
	// What it does, in the operator's language: this text goes onto the
	// confirmation card, and a person approves by reading it.
	Desc string
	// RequiresPausedRobots gates the operation on the real robots being paused.
	// Checked by the agent itself, not by the caller: the caller may be wrong or
	// out of date, the agent knows.
	RequiresPausedRobots bool
	// Args the operation understands. An argument outside this set is refused —
	// an unchecked argument is the hole through which a catalog becomes a shell.
	Args []string
}

// NeedsConfirm reports whether a human must approve this operation.
func (o Op) NeedsConfirm() bool { return o.Risk != RiskNone }

// Catalog is the WHOLE set of operations this agent can perform. Order matters
// only for display.
var Catalog = []Op{
	{ID: "status.processes", Risk: RiskNone,
		Desc: "процессы QUIK, раннера и агента: PID, память, время старта"},
	{ID: "status.system", Risk: RiskNone,
		Desc: "свободная память, место на диске, загрузка, uptime машины"},
	{ID: "status.windows", Risk: RiskNone,
		Desc: "заголовки открытых окон QUIK — видно, какие таблицы открыты"},
	{ID: "quik.dde_state", Risk: RiskNone,
		Desc: "по каждой таблице QUIK: включён ли вывод по DDE и когда обновлялась"},
	{ID: "log.tail", Risk: RiskNone, Args: []string{"file", "lines"},
		Desc: "хвост лога: info.log QUIK, лог раннера или лог агента"},
	{ID: "quik.settings", Risk: RiskNone,
		Desc: "текущие настройки QUIK: DDE-сервер, автозапуск Lua, таблицы, соединение"},

	{ID: "quik.open_table", Risk: RiskLow, Args: []string{"table"},
		Desc: "открыть таблицу QUIK и включить на ней вывод по DDE"},
	{ID: "quik.dde_setup", Risk: RiskLow, Args: []string{"tables"},
		Desc: "настроить вывод по DDE для рабочих таблиц разом (сервер и «выводить изменения»)"},
	{ID: "quik.lua_start", Risk: RiskLow,
		Desc: "запустить Lua-скрипт агента в QUIK (после старта он часто не запущен)"},
	{ID: "quik.profile_backup", Risk: RiskLow,
		Desc: "сохранить профиль окон и настроек QUIK в датированную копию"},
	{ID: "quik.set_option", Risk: RiskMedium, Args: []string{"key", "value"},
		Desc: "изменить ОДНУ известную настройку QUIK из списка допустимых"},
	{ID: "quik.profile_restore", Risk: RiskMedium, Args: []string{"name"},
		Desc: "вернуть профиль окон и настроек QUIK из сохранённой копии"},

	{ID: "runner.restart", Risk: RiskMedium,
		Desc: "перезапустить robot-runner.exe"},
	{ID: "agent.restart", Risk: RiskMedium,
		Desc: "перезапустить сам агент"},

	{ID: "quik.restart", Risk: RiskHigh, RequiresPausedRobots: true,
		Desc: "закрыть и запустить QUIK заново (лента переиграет день)"},
	{ID: "quik.reinstall", Risk: RiskHigh, RequiresPausedRobots: true,
		Args: []string{"sha256"},
		Desc: "переустановить QUIK из заранее положенного дистрибутива " +
			"(ключи, профиль окон и настройки DDE сохраняются и возвращаются)"},
	{ID: "vds.reboot", Risk: RiskHigh, RequiresPausedRobots: true,
		Desc: "перезагрузить машину"},
}

// SettableOptions — ЕДИНСТВЕННЫЕ настройки QUIK, которые агент меняет по команде.
// Ключ вне этого списка не применяется: «настройка терминала» без белого списка
// это та же произвольная запись в чужие файлы, только другими словами.
//
// Значения тоже проверяются — каждая настройка знает свой допустимый вид, иначе
// путь к дистрибутиву или имя DDE-сервера становятся местом для постороннего.
var SettableOptions = map[string]string{
	"dde.server":        "имя DDE-сервера (наш SHECTORY_QUIK)",
	"dde.send_changes":  "выводить изменения по мере поступления: 1 или 0",
	"lua.autostart":     "автозапуск Lua-скрипта агента при старте QUIK: 1 или 0",
	"conn.auto_login":   "автоматический вход при старте: 1 или 0",
	"conn.reconnect_sec": "период переподключения к серверу брокера, секунды",
}

// CheckOption refuses an unknown setting by name before anything is written.
func CheckOption(key string) error {
	if _, ok := SettableOptions[key]; !ok {
		keys := make([]string, 0, len(SettableOptions))
		for k := range SettableOptions {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		return fmt.Errorf("настройка %q не входит в список допустимых: %s",
			key, strings.Join(keys, ", "))
	}
	return nil
}

var byID = func() map[string]Op {
	m := make(map[string]Op, len(Catalog))
	for _, o := range Catalog {
		m[o.ID] = o
	}
	return m
}()

// Lookup returns the catalogued operation. An unknown id is a refusal, not a
// fallback: this is the whole reason the channel is safe to have.
func Lookup(id string) (Op, error) {
	o, ok := byID[id]
	if !ok {
		return Op{}, fmt.Errorf("операции %q нет в каталоге агента", id)
	}
	return o, nil
}

// CheckArgs refuses any argument the operation does not declare. Unchecked
// arguments are how a closed catalog quietly turns into a shell: "file" that walks
// out of the log directory, "table" that carries a command tail.
func (o Op) CheckArgs(args map[string]string) error {
	if len(args) == 0 {
		return nil
	}
	allowed := make(map[string]struct{}, len(o.Args))
	for _, a := range o.Args {
		allowed[a] = struct{}{}
	}
	unknown := make([]string, 0, len(args))
	for k := range args {
		if _, ok := allowed[k]; !ok {
			unknown = append(unknown, k)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return fmt.Errorf("операция %s не принимает аргументы: %s",
			o.ID, strings.Join(unknown, ", "))
	}
	return nil
}
