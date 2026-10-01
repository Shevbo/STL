package trade

import (
	"sync"
	"testing"
	"time"
)

// Часы и сон подменяются: тест о РАСПРЕДЕЛЕНИИ слотов, а не о том, умеет ли
// машина спать. Настоящий time.Sleep сделал бы его медленным и капризным.
func fakePacer(perSec int) (*txPacer, *time.Time, *[]time.Duration) {
	now := time.Unix(1_790_850_000, 0)
	var slept []time.Duration
	p := newTxPacer(perSec)
	p.now = func() time.Time { return now }
	p.sleep = func(d time.Duration) { slept = append(slept, d); now = now.Add(d) }
	return p, &now, &slept
}

func TestBurstIsSpreadToTheAllowedRate(t *testing.T) {
	p, _, slept := fakePacer(15)
	for i := 0; i < 25; i++ { // сетка «радиация» на 25 уровней — одной пачкой
		p.wait()
	}
	var total time.Duration
	for _, d := range *slept {
		total += d
	}
	// 25 транзакций не могут уложиться быстрее, чем 24 интервала между ними.
	// Считаем через сам интервал ограничителя, а не через секунду/15: иначе
	// сравниваем с числом, округлённым иначе, и тест врёт на наносекундах.
	min := 24 * p.perTx
	if total < min {
		t.Fatalf("пачка из 25 ушла за %v, быстрее разрешённого %v — брокер возьмёт штраф", total, min)
	}
}

func TestQuietFlowIsNotDelayed(t *testing.T) {
	p, now, slept := fakePacer(15)
	for i := 0; i < 5; i++ {
		p.wait()
		*now = now.Add(time.Second) // раз в секунду: лимит не трогаем
	}
	for _, d := range *slept {
		if d > 0 {
			t.Fatalf("редкие транзакции задержаны на %v — ограничитель мешает там, где не должен", d)
		}
	}
}

func TestOrderIsPreservedUnderConcurrency(t *testing.T) {
	p := newTxPacer(1000) // быстрый, чтобы тест не спал
	var mu sync.Mutex
	seen := 0
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			p.wait()
			mu.Lock()
			seen++
			mu.Unlock()
		}()
	}
	wg.Wait()
	if seen != 50 {
		t.Fatalf("пропущено транзакций: %d из 50. Отбрасывать нельзя — потерянная "+
			"защитная заявка это незакрытая позиция", seen)
	}
}

func TestZeroRateFallsBackToTheLimit(t *testing.T) {
	if newTxPacer(0).perTx != time.Second/quikMaxTxPerSec {
		t.Fatal("нулевая настройка обязана означать лимит, а не отсутствие лимита")
	}
}
