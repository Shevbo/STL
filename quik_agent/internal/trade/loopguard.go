package trade

import (
	"fmt"
	"strings"
	"sync"
)

// Предохранитель от зацикливания заявок.
//
// 30.09.2026 робот agent-macdshort-RIU6-v1 получил от брокера 26 одинаковых
// отказов подряд — «[GW][332] Нехватка средств по лимитам клиента» — по одному
// в минуту, полчаса. Он пытался продать, брокер отказывал, робот пробовал
// снова: ни робот, ни STL не считали повторы, потому что каждая попытка по
// отдельности законна. Оператор потребовал это останавливать: брокер берёт 4
// рубля за транзакцию сверх 15-20 в секунду, а цикл отказов — это ещё и
// признак, что источник заявок живёт в неверной картине мира.
//
// Правило простое и намеренно тупое: N отказов подряд ОТ ОДНОГО ИСТОЧНИКА с
// ОДНОЙ причиной — источник замолкает на cooldown. Успешная транзакция сбрасывает
// счётчик: значит картина мира сошлась. Замолкает ИСТОЧНИК, а не весь агент —
// один сломавшийся робот не имеет права остановить чужую защиту.
const (
	loopRejectLimit  = 5                // отказов подряд до блокировки источника
	loopCooldownMs   = 15 * 60 * 1000   // на сколько замолкает источник
	loopReasonMaxLen = 60               // по скольким символам текста судим «та же причина»
)

type loopState struct {
	reason   string
	count    int
	blockTil int64
}

// LoopGuard считает подряд идущие отказы по источнику заявок.
type LoopGuard struct {
	mu    sync.Mutex
	src   map[string]*loopState
	nowMs func() int64
}

func NewLoopGuard(nowMs func() int64) *LoopGuard {
	return &LoopGuard{src: map[string]*loopState{}, nowMs: nowMs}
}

// loopSource: чей это поток заявок. Робот — сам робот, умная заявка — сама
// заявка, остальное — «рука». Считать по client_id нельзя: он уникален на
// каждую попытку, и цикл никогда бы не собрался.
func loopSource(clientID string) string {
	if rid, ok := RobotIDFromClientID(clientID); ok {
		return "robot:" + rid
	}
	if soID, ok := strings.CutPrefix(clientID, "so:"); ok && soID != "" {
		if i := strings.IndexByte(soID, ':'); i > 0 {
			soID = soID[:i] // so:<id>:<уровень/фаза> — источник это заявка целиком
		}
		return "so:" + soID
	}
	if strings.HasPrefix(clientID, "recon:") {
		return "recon"
	}
	return "manual"
}

func reasonKey(text string) string {
	t := strings.TrimSpace(text)
	if len(t) > loopReasonMaxLen {
		t = t[:loopReasonMaxLen]
	}
	return t
}

// Reject регистрирует отказ. Возвращает (заблокирован ли источник ТЕПЕРЬ,
// сколько отказов подряд, причина) — блокировка наступает ровно на N-м.
func (g *LoopGuard) Reject(clientID, text string) (bool, int, string) {
	if g == nil {
		return false, 0, ""
	}
	key, reason := loopSource(clientID), reasonKey(text)
	g.mu.Lock()
	defer g.mu.Unlock()
	st := g.src[key]
	if st == nil || st.reason != reason {
		st = &loopState{reason: reason}
		g.src[key] = st
	}
	st.count++
	if st.count >= loopRejectLimit && st.blockTil == 0 {
		st.blockTil = g.nowMs() + loopCooldownMs
		return true, st.count, reason
	}
	return false, st.count, reason
}

// Accept: транзакция прошла — картина мира сошлась, счётчик сбрасывается.
func (g *LoopGuard) Accept(clientID string) {
	if g == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.src, loopSource(clientID))
}

// Blocked: молчит ли источник прямо сейчас (и до какого времени).
func (g *LoopGuard) Blocked(clientID string) (bool, int64) {
	if g == nil {
		return false, 0
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	st := g.src[loopSource(clientID)]
	if st == nil || st.blockTil == 0 {
		return false, 0
	}
	if g.nowMs() >= st.blockTil {
		delete(g.src, loopSource(clientID))
		return false, 0
	}
	return true, st.blockTil
}

// Reason: человеческий текст отказа для журнала и алерта.
func (g *LoopGuard) Reason(clientID string) string {
	if g == nil {
		return ""
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if st := g.src[loopSource(clientID)]; st != nil {
		return fmt.Sprintf("%d отказов подряд: %s", st.count, st.reason)
	}
	return ""
}
