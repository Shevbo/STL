package commission

import (
	"errors"
	"math"
	"testing"
)

func TestCoef(t *testing.T) {
	cases := []struct {
		name      string
		priceStep float64
		stepCost  float64
		want      float64
		wantErr   error
	}{
		// RI (RTS index future): step 10 points, step cost ~13.3 RUB -> coef 1.33.
		{"RI", 10, 13.3, 1.33, nil},
		// Si (USD/RUB future): step 1, step cost 1 -> coef 1.
		{"Si", 1, 1, 1, nil},
		// BR (Brent): step 0.01, step cost ~0.66 -> coef 66.
		{"BR", 0.01, 0.66, 66, nil},
		// Sub-unit step cost.
		{"fractional", 5, 2.5, 0.5, nil},
		// Zero step cost is allowed (degenerate but valid): coef 0.
		{"zero_step_cost", 10, 0, 0, nil},
		// Invalid price steps.
		{"zero_step", 0, 5, 0, ErrInvalidStep},
		{"negative_step", -1, 5, 0, ErrInvalidStep},
		{"nan_step", math.NaN(), 5, 0, ErrInvalidStep},
		{"inf_step", math.Inf(1), 5, 0, ErrInvalidStep},
		// Invalid step costs.
		{"negative_cost", 10, -1, 0, ErrInvalidStepCost},
		{"nan_cost", 10, math.NaN(), 0, ErrInvalidStepCost},
		{"inf_cost", 10, math.Inf(1), 0, ErrInvalidStepCost},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Coef(tc.priceStep, tc.stepCost)
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("err = %v, want %v", err, tc.wantErr)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected err: %v", err)
			}
			if math.Abs(got-tc.want) > 1e-9 {
				t.Fatalf("coef = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestCoefOrZero(t *testing.T) {
	if v := CoefOrZero(10, 13.3); math.Abs(v-1.33) > 1e-9 {
		t.Fatalf("CoefOrZero valid = %v, want 1.33", v)
	}
	if v := CoefOrZero(0, 13.3); v != 0 {
		t.Fatalf("CoefOrZero invalid = %v, want 0", v)
	}
}
