package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	application "example.com/go-checkout/internal/application/checkout"
	"example.com/go-checkout/internal/config"
	"example.com/go-checkout/internal/repository/memory"
	"example.com/go-checkout/internal/transport/httpapi"
)

func main() {
	cfg := config.Load()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	store := memory.NewStore()
	service := application.NewService(store, store, store, store)
	server := &http.Server{Addr: cfg.Address, Handler: httpapi.New(service, logger), ReadTimeout: cfg.ReadTimeout, WriteTimeout: cfg.WriteTimeout}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	logger.Info("checkout service started", "address", cfg.Address)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		logger.Error("server failed", "error", err)
		os.Exit(1)
	}
}
