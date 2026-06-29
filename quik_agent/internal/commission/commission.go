// Package commission derives the FORTS commission coefficient from QUIK params.
//
// On FORTS the per-contract money value of one price step is "Ст. шага цены"
// (step cost) and the price granularity is "Шаг цены" (price step). The
// commission/PnL coefficient that converts a price delta in points into money is:
//
//	coef = step_cost / price_step
//
// This is the taker reference used for backtests (see reference_commission_model).
// Phase 1 is read-only: this package only computes the coefficient from data the
// agent already reads over DDE. It places no orders and charges nothing.
package commission

import (
	"errors"
	"math"
)

// ErrInvalidStep is returned when price_step is zero, negative, or non-finite.
var ErrInvalidStep = errors.New("commission: price_step must be a positive finite number")

// ErrInvalidStepCost is returned when step_cost is negative or non-finite.
var ErrInvalidStepCost = errors.New("commission: step_cost must be a non-negative finite number")

// Coef returns step_cost / price_step.
//
// It returns ErrInvalidStep if priceStep is <= 0 or not finite, and
// ErrInvalidStepCost if stepCost is < 0 or not finite.
func Coef(priceStep, stepCost float64) (float64, error) {
	if math.IsNaN(priceStep) || math.IsInf(priceStep, 0) || priceStep <= 0 {
		return 0, ErrInvalidStep
	}
	if math.IsNaN(stepCost) || math.IsInf(stepCost, 0) || stepCost < 0 {
		return 0, ErrInvalidStepCost
	}
	return stepCost / priceStep, nil
}

// CoefOrZero is a convenience wrapper that returns 0 on any error.
// Use it on hot read-only paths where an invalid row should be skipped silently.
func CoefOrZero(priceStep, stepCost float64) float64 {
	c, err := Coef(priceStep, stepCost)
	if err != nil {
		return 0
	}
	return c
}
