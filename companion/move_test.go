package main

import "testing"

// Два монитора: главный 1920x1080 и второй слева, поэтому Left отрицательный.
var twoScreens = rectT{Left: -1920, Top: 0, Right: 1920, Bottom: 1080}

func TestClampInKeepsPanelReachable(t *testing.T) {
	const w, h = 350, 400
	cases := []struct {
		name  string
		x, y  int
		wantX int
		wantY int
	}{
		{"обычное место не трогаем", 500, 300, 500, 300},
		{"соседний монитор слева разрешён", -1500, 200, -1500, 200},
		{"за левый край: остаётся полоска", -5000, 200, -1920 - w + dragKeepVisible, 200},
		{"за правый край: остаётся полоска", 5000, 200, 1920 - dragKeepVisible, 200},
		{"вверх не пускаем: тащат за шапку", 500, -300, 500, 0},
		{"вниз: край панели виден", 500, 5000, 500, 1080 - dragKeepVisible},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			x, y := clampIn(twoScreens, c.x, c.y, w, h)
			if x != c.wantX || y != c.wantY {
				t.Fatalf("clampIn(%d,%d) = %d,%d; ожидалось %d,%d", c.x, c.y, x, y, c.wantX, c.wantY)
			}
		})
	}
}

// Смещение влево и вверх отрицательно, а параметры сообщения беззнаковые: без
// пары pack/unpack -1 превратился бы в четыре миллиарда и утащил окно.
func TestPackIntRoundTripsNegative(t *testing.T) {
	for _, v := range []int{0, 1, -1, 37, -37, 1920, -1920} {
		if got := unpackInt(packInt(v)); got != v {
			t.Fatalf("unpackInt(packInt(%d)) = %d", v, got)
		}
	}
}
