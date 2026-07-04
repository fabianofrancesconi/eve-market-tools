import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UiState {
  lpSearch: string
  lpCorp: string
  lpBudget: number
  lpSalesTax: number
  lpBrokerFee: number
  lpMaxSpread: number
  lpStationId: number
  lpHideIlliquid: boolean
  lpHideUnaffordable: boolean
  lpTradeWeight: number
  lpSortKey: string
  lpSortDir: 'asc' | 'desc'
  lpColOrder: string[]
  lpColWidths: Record<string, number>
  arbRegion: number
  arbMinIsk: number
  arbSalesTax: number
  arbMode: 'cross' | 'same'
  arbMaxJumps: number
  arbAvoidLowsec: boolean
  arbRouteFlag: string
  indGroupIds: string[]
  indStationId: number
  indSalesTax: number
  indBrokerFee: number
  indJobRate: number
  indRuns: number
  indBuildableOnly: boolean
  indHideT2: boolean
  indIncludeUnobtainable: boolean
  indMinTradeability: string
  indTradeWeight: string
  setLpSearch: (v: string) => void
  setLpCorp: (v: string) => void
  setLpBudget: (v: number) => void
  setLpSalesTax: (v: number) => void
  setLpBrokerFee: (v: number) => void
  setLpMaxSpread: (v: number) => void
  setLpStationId: (v: number) => void
  setLpHideIlliquid: (v: boolean) => void
  setLpHideUnaffordable: (v: boolean) => void
  setLpTradeWeight: (v: number) => void
  setLpSortKey: (v: string) => void
  setLpSortDir: (v: 'asc' | 'desc') => void
  setLpColOrder: (v: string[]) => void
  setLpColWidths: (v: Record<string, number>) => void
  setArbRegion: (v: number) => void
  setArbMinIsk: (v: number) => void
  setArbSalesTax: (v: number) => void
  setArbMode: (v: 'cross' | 'same') => void
  setArbMaxJumps: (v: number) => void
  setArbAvoidLowsec: (v: boolean) => void
  setArbRouteFlag: (v: string) => void
  setIndGroupIds: (v: string[]) => void
  setIndStationId: (v: number) => void
  setIndSalesTax: (v: number) => void
  setIndBrokerFee: (v: number) => void
  setIndJobRate: (v: number) => void
  setIndRuns: (v: number) => void
  setIndBuildableOnly: (v: boolean) => void
  setIndHideT2: (v: boolean) => void
  setIndIncludeUnobtainable: (v: boolean) => void
  setIndMinTradeability: (v: string) => void
  setIndTradeWeight: (v: string) => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      lpSearch: '',
      lpCorp: '',
      lpBudget: 100000,
      lpSalesTax: 7.5,
      lpBrokerFee: 3.0,
      lpMaxSpread: 100,
      lpStationId: 60003760,
      lpHideIlliquid: false,
      lpHideUnaffordable: false,
      lpTradeWeight: 0.5,
      lpSortKey: 'isk_per_lp_patient',
      lpSortDir: 'desc',
      lpColOrder: [],
      lpColWidths: {},
      arbRegion: 10000002,
      arbMinIsk: 1000000,
      arbSalesTax: 7.5,
      arbMode: 'cross',
      arbMaxJumps: 6,
      arbAvoidLowsec: false,
      arbRouteFlag: 'shortest',
      indGroupIds: [],
      indStationId: 60003760,
      indSalesTax: 7.5,
      indBrokerFee: 3.0,
      indJobRate: 6.0,
      indRuns: 1,
      indBuildableOnly: false,
      indHideT2: false,
      indIncludeUnobtainable: false,
      indMinTradeability: '',
      indTradeWeight: 'balanced',
      setLpSearch: (v) => set({ lpSearch: v }),
      setLpCorp: (v) => set({ lpCorp: v }),
      setLpBudget: (v) => set({ lpBudget: v }),
      setLpSalesTax: (v) => set({ lpSalesTax: v }),
      setLpBrokerFee: (v) => set({ lpBrokerFee: v }),
      setLpMaxSpread: (v) => set({ lpMaxSpread: v }),
      setLpStationId: (v) => set({ lpStationId: v }),
      setLpHideIlliquid: (v) => set({ lpHideIlliquid: v }),
      setLpHideUnaffordable: (v) => set({ lpHideUnaffordable: v }),
      setLpTradeWeight: (v) => set({ lpTradeWeight: v }),
      setLpSortKey: (v) => set({ lpSortKey: v }),
      setLpSortDir: (v) => set({ lpSortDir: v }),
      setLpColOrder: (v) => set({ lpColOrder: v }),
      setLpColWidths: (v) => set({ lpColWidths: v }),
      setArbRegion: (v) => set({ arbRegion: v }),
      setArbMinIsk: (v) => set({ arbMinIsk: v }),
      setArbSalesTax: (v) => set({ arbSalesTax: v }),
      setArbMode: (v) => set({ arbMode: v }),
      setArbMaxJumps: (v) => set({ arbMaxJumps: v }),
      setArbAvoidLowsec: (v) => set({ arbAvoidLowsec: v }),
      setArbRouteFlag: (v) => set({ arbRouteFlag: v }),
      setIndGroupIds: (v) => set({ indGroupIds: v }),
      setIndStationId: (v) => set({ indStationId: v }),
      setIndSalesTax: (v) => set({ indSalesTax: v }),
      setIndBrokerFee: (v) => set({ indBrokerFee: v }),
      setIndJobRate: (v) => set({ indJobRate: v }),
      setIndRuns: (v) => set({ indRuns: v }),
      setIndBuildableOnly: (v) => set({ indBuildableOnly: v }),
      setIndHideT2: (v) => set({ indHideT2: v }),
      setIndIncludeUnobtainable: (v) => set({ indIncludeUnobtainable: v }),
      setIndMinTradeability: (v) => set({ indMinTradeability: v }),
      setIndTradeWeight: (v) => set({ indTradeWeight: v }),
    }),
    {
      name: 'eve-scanner',
      partialize: (state) => ({
        lpCorp: state.lpCorp,
        lpBudget: state.lpBudget,
        lpSalesTax: state.lpSalesTax,
        lpBrokerFee: state.lpBrokerFee,
        lpMaxSpread: state.lpMaxSpread,
        lpStationId: state.lpStationId,
        lpHideIlliquid: state.lpHideIlliquid,
        lpHideUnaffordable: state.lpHideUnaffordable,
        lpTradeWeight: state.lpTradeWeight,
        lpSortKey: state.lpSortKey,
        lpSortDir: state.lpSortDir,
        lpColOrder: state.lpColOrder,
        lpColWidths: state.lpColWidths,
        arbRegion: state.arbRegion,
        arbMinIsk: state.arbMinIsk,
        arbSalesTax: state.arbSalesTax,
        arbMode: state.arbMode,
        arbMaxJumps: state.arbMaxJumps,
        arbAvoidLowsec: state.arbAvoidLowsec,
        arbRouteFlag: state.arbRouteFlag,
        indGroupIds: state.indGroupIds,
        indStationId: state.indStationId,
        indSalesTax: state.indSalesTax,
        indBrokerFee: state.indBrokerFee,
        indJobRate: state.indJobRate,
        indRuns: state.indRuns,
        indBuildableOnly: state.indBuildableOnly,
        indHideT2: state.indHideT2,
        indIncludeUnobtainable: state.indIncludeUnobtainable,
        indMinTradeability: state.indMinTradeability,
        indTradeWeight: state.indTradeWeight,
      }),
    }
  )
)
