import AppKit
import SwiftUI
import AVFoundation
import UniformTypeIdentifiers

// MARK: - App Bootstrap

class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        let contentView = MainView()
            .environmentObject(AppState())

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "AMT Music Generator"
        window.center()
        window.contentView = NSHostingView(rootView: contentView)
        window.makeKeyAndOrderFront(nil)
        window.appearance = NSAppearance(named: .darkAqua)
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0.08, green: 0.08, blue: 0.12, alpha: 1)

        // Build a minimal menu bar
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit AMT Generator",
                        action: #selector(NSApplication.terminate(_:)),
                        keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        NSApp.mainMenu = mainMenu

        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

let delegate = AppDelegate()
NSApplication.shared.delegate = delegate
NSApp.run()


// MARK: - State

enum GenerationStage: String {
    case idle = "Ready"
    case extracting = "Extracting MIDI..."
    case converting = "Converting to AMT tokens..."
    case loadingModel = "Loading model..."
    case generating = "Generating..."
    case convertingOutput = "Converting output..."
    case done = "Done"
    case failed = "Failed"
}

struct MIDINote: Identifiable {
    let id = UUID()
    let startTick: Int
    let pitch: Int
    let velocity: Int
    let durationTicks: Int
    let channel: Int
    let trackIndex: Int
    let program: Int
}

struct InstrumentTrack: Identifiable {
    let id = UUID()
    let program: Int
    let channel: Int
    let name: String
    let notes: [MIDINote]
    var midiPath: String?
}

struct TrackInfo: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let noteCount: Int
    let type: String       // "midi" or "logicx"
    // MIDI tracks
    let index: Int?
    let program: Int?
    // Logic tracks
    let trackId: Int?
    let subId: String?
    let duration: Double?
    // Preview
    let midiPath: String?
    var midiNotes: [MIDINote] = []

    func hash(into hasher: inout Hasher) { hasher.combine(id) }
    static func == (lhs: TrackInfo, rhs: TrackInfo) -> Bool { lhs.id == rhs.id }

    var displayName: String {
        if type == "midi" {
            let prog = program.map { gmName(program: $0) } ?? ""
            return "\(name)\(prog.isEmpty ? "" : " (\(prog))") - \(noteCount) notes"
        } else {
            let dur = duration.map { String(format: "%.1fs", $0) } ?? ""
            return "\(name) - \(noteCount) notes\(dur.isEmpty ? "" : ", \(dur)")"
        }
    }
}

func gmName(program: Int) -> String {
    let names = [
        // Piano (0-7)
        "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
        "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
        "Clavinet",
        // Chromatic Percussion (8-15)
        "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
        "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
        // Organ (16-23)
        "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
        "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
        // Guitar (24-31)
        "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Guitar",
        "Muted Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
        // Bass (32-39)
        "Acoustic Bass", "Finger Bass", "Pick Bass", "Fretless Bass",
        "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
        // Strings (40-47)
        "Violin", "Viola", "Cello", "Contrabass",
        "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
        // Ensemble (48-55)
        "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
        "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
        // Brass (56-63)
        "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
        "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
        // Reed (64-71)
        "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
        "Oboe", "English Horn", "Bassoon", "Clarinet",
        // Pipe (72-79)
        "Piccolo", "Flute", "Recorder", "Pan Flute",
        "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
        // Synth Lead (80-87)
        "Square Lead", "Sawtooth Lead", "Calliope Lead", "Chiff Lead",
        "Charang Lead", "Voice Lead", "Fifths Lead", "Bass + Lead",
        // Synth Pad (88-95)
        "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad",
        "Bowed Pad", "Metallic Pad", "Halo Pad", "Sweep Pad",
        // Synth Effects (96-103)
        "Rain", "Soundtrack", "Crystal", "Atmosphere",
        "Brightness", "Goblins", "Echoes", "Sci-Fi",
        // Ethnic (104-111)
        "Sitar", "Banjo", "Shamisen", "Koto",
        "Kalimba", "Bagpipe", "Fiddle", "Shanai",
        // Percussive (112-119)
        "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
        "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
        // Sound Effects (120-127)
        "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
        "Telephone Ring", "Helicopter", "Applause", "Gunshot",
    ]
    if program >= 0 && program < names.count { return names[program] }
    return ""
}

class AppState: ObservableObject {
    @Published var inputPath: String?
    @Published var inputName: String = ""
    @Published var inputInfo: String = ""

    // Tracks
    @Published var tracks: [TrackInfo] = []
    @Published var selectedTrack: TrackInfo?
    @Published var loadingTracks: Bool = false

    // Parameters
    @Published var numEvents: Double = 100
    @Published var topP: Double = 0.98
    @Published var temperature: Double = 1.20
    @Published var promptSeconds: Double = 10
    @Published var autoPrompt: Bool = true
    @Published var modelSize: String = "small"

    // Generation
    @Published var stage: GenerationStage = .idle
    @Published var progressText: String = ""
    @Published var errorText: String = ""
    @Published var isRunning: Bool = false

    // Output
    @Published var outputPath: String?
    @Published var parsedNotes: [MIDINote] = []
    @Published var instrumentTracks: [InstrumentTrack] = []
    @Published var outputDuration: Double = 0
    @Published var outputPlaybackProgress: Double = 0
    @Published var outputLeadInSec: Double = 0

    // Playback
    @Published var isPlaying: Bool = false
    var midiPlayer: AVMIDIPlayer?
    private var midiPlayerPath: String?

    // Track preview playback (input track selector + output instruments)
    @Published var playingTrackId: UUID?
    var trackPlayer: AVMIDIPlayer?
    @Published var trackPlaybackProgress: Double = 0

    @Published var playingInstrumentId: UUID?
    var instrumentPlayer: AVMIDIPlayer?
    @Published var instrumentPlaybackProgress: Double = 0

    private var playbackTimer: Timer?

    var outputPlayableDuration: Double {
        max(outputDuration - outputLeadInSec, 0)
    }

    var outputCurrentTime: Double {
        let currentFileTime = outputPlaybackProgress * max(outputDuration, 0)
        return min(max(currentFileTime - outputLeadInSec, 0), outputPlayableDuration)
    }

    func resetOutputPlayback() {
        midiPlayer?.stop()
        midiPlayer = nil
        midiPlayerPath = nil
        isPlaying = false
        outputPlaybackProgress = 0
        outputLeadInSec = 0
        stopPlaybackTimerIfNeeded()
    }

    func configureOutputPlayback(path: String?, notes: [MIDINote], duration: Double) {
        outputPath = path
        parsedNotes = notes
        outputDuration = duration
        outputLeadInSec = 0
        outputPlaybackProgress = 0

        guard duration > 0,
              let minTick = notes.map(\.startTick).min(),
              let maxTick = notes.map({ $0.startTick + $0.durationTicks }).max(),
              maxTick > 0 else {
            resetPreparedOutputPlayer()
            return
        }

        outputLeadInSec = duration * Double(minTick) / Double(maxTick)
        outputLeadInSec = min(max(outputLeadInSec, 0), duration)
        primeOutputPlayer()
    }

    func toggleOutputPlayback() {
        if isPlaying {
            stopOutputPlayback(resetPosition: true)
        } else {
            playOutput()
        }
    }

    func seekOutput(to timelineSec: Double) {
        guard prepareOutputPlayer() else { return }
        let filePosition = outputFilePosition(for: timelineSec)
        midiPlayer?.currentPosition = filePosition
        if outputDuration > 0 {
            outputPlaybackProgress = min(max(filePosition / outputDuration, 0), 1)
        } else {
            outputPlaybackProgress = 0
        }
    }

    private func playOutput() {
        guard prepareOutputPlayer(), let player = midiPlayer else { return }
        if player.currentPosition < outputLeadInSec || player.currentPosition >= outputDuration {
            player.currentPosition = outputLeadInSec
        }
        if outputDuration > 0 {
            outputPlaybackProgress = min(max(player.currentPosition / outputDuration, 0), 1)
        } else {
            outputPlaybackProgress = 0
        }
        isPlaying = true
        startPlaybackTimer()
        player.play { [weak self] in
            DispatchQueue.main.async {
                self?.finishOutputPlayback()
            }
        }
    }

    private func stopOutputPlayback(resetPosition: Bool) {
        midiPlayer?.stop()
        isPlaying = false
        if resetPosition {
            midiPlayer?.currentPosition = outputLeadInSec
            outputPlaybackProgress = 0
        } else if let player = midiPlayer, outputDuration > 0 {
            outputPlaybackProgress = min(max(player.currentPosition / outputDuration, 0), 1)
        }
        stopPlaybackTimerIfNeeded()
    }

    private func finishOutputPlayback() {
        isPlaying = false
        midiPlayer?.currentPosition = outputLeadInSec
        outputPlaybackProgress = 0
        stopPlaybackTimerIfNeeded()
    }

    private func resetPreparedOutputPlayer() {
        midiPlayer?.stop()
        midiPlayer = nil
        midiPlayerPath = nil
        isPlaying = false
        outputPlaybackProgress = 0
    }

    private func primeOutputPlayer() {
        guard prepareOutputPlayer() else { return }
        midiPlayer?.currentPosition = outputLeadInSec
        outputPlaybackProgress = 0
    }

    @discardableResult
    private func prepareOutputPlayer() -> Bool {
        guard let path = outputPath else {
            resetPreparedOutputPlayer()
            return false
        }

        if midiPlayer == nil || midiPlayerPath != path {
            let url = URL(fileURLWithPath: path)
            do {
                let player = try AVMIDIPlayer(contentsOf: url, soundBankURL: nil)
                player.prepareToPlay()
                midiPlayer = player
                midiPlayerPath = path
            } catch {
                resetPreparedOutputPlayer()
                return false
            }
        }

        return midiPlayer != nil
    }

    private func outputFilePosition(for timelineSec: Double) -> Double {
        let clampedTimeline = min(max(timelineSec, 0), outputPlayableDuration)
        return min(max(outputLeadInSec + clampedTimeline, 0), outputDuration)
    }

    func playTrack(_ track: TrackInfo) {
        trackPlayer?.stop()
        trackPlayer = nil
        guard let path = track.midiPath else { return }
        let url = URL(fileURLWithPath: path)
        do {
            let player = try AVMIDIPlayer(contentsOf: url, soundBankURL: nil)
            trackPlayer = player
            player.prepareToPlay()
            playingTrackId = track.id
            trackPlaybackProgress = 0
            startPlaybackTimer()
            player.play { [weak self] in
                DispatchQueue.main.async {
                    guard self?.trackPlayer === player else { return }
                    self?.playingTrackId = nil
                    self?.trackPlaybackProgress = 0
                    self?.stopPlaybackTimerIfNeeded()
                }
            }
        } catch {
            playingTrackId = nil
        }
    }

    func stopTrackPlayback() {
        trackPlayer?.stop()
        trackPlayer = nil
        playingTrackId = nil
        trackPlaybackProgress = 0
        stopPlaybackTimerIfNeeded()
    }

    func playInstrument(_ inst: InstrumentTrack) {
        instrumentPlayer?.stop()
        instrumentPlayer = nil
        guard let path = inst.midiPath else { return }
        let url = URL(fileURLWithPath: path)
        do {
            let player = try AVMIDIPlayer(contentsOf: url, soundBankURL: nil)
            instrumentPlayer = player
            player.prepareToPlay()
            playingInstrumentId = inst.id
            instrumentPlaybackProgress = 0
            startPlaybackTimer()
            player.play { [weak self] in
                DispatchQueue.main.async {
                    guard self?.instrumentPlayer === player else { return }
                    self?.playingInstrumentId = nil
                    self?.instrumentPlaybackProgress = 0
                    self?.stopPlaybackTimerIfNeeded()
                }
            }
        } catch {
            playingInstrumentId = nil
        }
    }

    func stopInstrumentPlayback() {
        instrumentPlayer?.stop()
        instrumentPlayer = nil
        playingInstrumentId = nil
        instrumentPlaybackProgress = 0
        stopPlaybackTimerIfNeeded()
    }

    private func startPlaybackTimer() {
        playbackTimer?.invalidate()
        playbackTimer = Timer.scheduledTimer(withTimeInterval: 1.0/30.0,
                                             repeats: true) { [weak self] _ in
            guard let self else { return }
            if let player = self.midiPlayer, self.isPlaying,
               self.outputDuration > 0 {
                self.outputPlaybackProgress = min(
                    max(player.currentPosition / self.outputDuration, 0), 1)
            }
            if let player = self.trackPlayer, self.playingTrackId != nil,
               player.duration > 0 {
                self.trackPlaybackProgress = player.currentPosition / player.duration
            }
            if let player = self.instrumentPlayer, self.playingInstrumentId != nil,
               player.duration > 0 {
                self.instrumentPlaybackProgress = player.currentPosition / player.duration
            }
        }
    }

    private func stopPlaybackTimerIfNeeded() {
        if !isPlaying && playingTrackId == nil && playingInstrumentId == nil {
            playbackTimer?.invalidate()
            playbackTimer = nil
        }
    }
}


// MARK: - Theme

enum Theme {
    static let bg = Color(red: 0.08, green: 0.08, blue: 0.12)
    static let cardBg = Color(red: 0.12, green: 0.12, blue: 0.17)
    static let cardBorder = Color.white.opacity(0.06)
    static let accent = Color(red: 0.35, green: 0.5, blue: 1.0)
    static let accentGradient = LinearGradient(
        colors: [Color(red: 0.35, green: 0.45, blue: 1.0),
                 Color(red: 0.55, green: 0.35, blue: 1.0)],
        startPoint: .leading, endPoint: .trailing)
    static let subtleText = Color.white.opacity(0.45)
    static let bodyText = Color.white.opacity(0.85)
    static let heading = Color.white.opacity(0.95)
}

struct SectionCard<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        content
            .padding(16)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(Theme.cardBg)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Theme.cardBorder, lineWidth: 1)
            )
    }
}

struct SectionHeader: View {
    let title: String
    let icon: String
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .foregroundColor(Theme.accent)
                .font(.system(size: 13, weight: .semibold))
            Text(title)
                .font(.system(size: 13, weight: .semibold, design: .rounded))
                .foregroundColor(Theme.heading)
                .textCase(.uppercase)
                .tracking(0.8)
        }
    }
}


// MARK: - Main View

struct MainView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                FileInputSection()

                if !state.tracks.isEmpty {
                    TrackSelectorSection()
                }

                ParameterSection()

                GenerateSection()

                if state.outputPath != nil {
                    OutputSection()
                }
            }
            .padding(20)
        }
        .background(Theme.bg)
        .frame(minWidth: 520, minHeight: 620)
    }
}


// MARK: - File Input

struct FileInputSection: View {
    @EnvironmentObject var state: AppState
    @State private var isDropTarget = false

    var body: some View {
        SectionCard {
            VStack(alignment: .leading, spacing: 10) {
                SectionHeader(title: "Input", icon: "doc.badge.plus")

                HStack(spacing: 10) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10)
                            .fill(isDropTarget
                                  ? Theme.accent.opacity(0.12)
                                  : Color.white.opacity(0.03))
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
                            .foregroundColor(isDropTarget
                                             ? Theme.accent.opacity(0.6)
                                             : Color.white.opacity(0.15))
                            .frame(height: 56)

                        if state.inputPath != nil {
                            HStack(spacing: 8) {
                                Image(systemName: "music.note.list")
                                    .foregroundColor(Theme.accent)
                                Text(state.inputName)
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundColor(Theme.bodyText)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        } else {
                            HStack(spacing: 6) {
                                Image(systemName: "arrow.down.doc")
                                    .foregroundColor(Theme.subtleText)
                                Text("Drop .logicx or .mid file")
                                    .font(.system(size: 13))
                                    .foregroundColor(Theme.subtleText)
                            }
                        }
                    }
                    .onDrop(of: [.fileURL], isTargeted: $isDropTarget) { providers in
                        handleDrop(providers)
                    }

                    Button(action: openFile) {
                        Text("Open")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 8)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color.white.opacity(0.1))
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .stroke(Color.white.opacity(0.15), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }

                if !state.inputInfo.isEmpty {
                    Text(state.inputInfo)
                        .font(.system(size: 11))
                        .foregroundColor(Theme.subtleText)
                }
            }
        }
    }

    func openFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = false
        panel.message = "Select a Logic Pro project or MIDI file"

        if panel.runModal() == .OK, let url = panel.url {
            selectFile(url)
        }
    }

    func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        provider.loadItem(forTypeIdentifier: "public.file-url", options: nil) { item, _ in
            guard let data = item as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil)
            else { return }
            DispatchQueue.main.async {
                selectFile(url)
            }
        }
        return true
    }

    func selectFile(_ url: URL) {
        let path = url.path
        let ext = url.pathExtension.lowercased()
        guard ext == "mid" || ext == "midi" || ext == "logicx" else {
            state.inputInfo = "Unsupported file type: .\(ext)"
            return
        }
        state.inputPath = path
        state.inputName = url.lastPathComponent
        state.resetOutputPlayback()
        state.parsedNotes = []
        state.tracks = []
        state.selectedTrack = nil

        // Load tracks via pipeline --list-tracks
        loadTracks(path: path)
    }

    func loadTracks(path: String) {
        state.loadingTracks = true
        state.inputInfo = "Scanning tracks..."
        DispatchQueue.global().async {
            let proc = PythonEnvironment.process(
                arguments: [PythonEnvironment.script("amt_pipeline.py"), path, "--list-tracks"])

            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            do {
                try proc.run()
            } catch {
                DispatchQueue.main.async {
                    state.tracks = []
                    state.loadingTracks = false
                    state.inputInfo = "Could not start Python at \(PythonEnvironment.python) — see README"
                }
                return
            }
            proc.waitUntilExit()

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let json = String(data: data, encoding: .utf8) ?? "[]"

            // Parse JSON
            var tracks: [TrackInfo] = []
            if let jsonData = json.data(using: .utf8),
               let arr = try? JSONSerialization.jsonObject(with: jsonData) as? [[String: Any]] {
                for item in arr {
                    let type = item["type"] as? String ?? ""
                    let noteCount = item["notes"] as? Int ?? 0
                    let name = item["name"] as? String ?? "Unknown"
                    let midiPath = item["midi_path"] as? String

                    // Parse notes from the temp MIDI file for visualization
                    let midiNotes: [MIDINote]
                    if let mp = midiPath {
                        midiNotes = MIDIParser.parse(path: mp)
                    } else {
                        midiNotes = []
                    }

                    let track = TrackInfo(
                        name: name,
                        noteCount: noteCount,
                        type: type,
                        index: item["index"] as? Int,
                        program: item["program"] as? Int,
                        trackId: item["track_id"] as? Int,
                        subId: item["sub_id"] as? String,
                        duration: item["duration"] as? Double,
                        midiPath: midiPath,
                        midiNotes: midiNotes
                    )
                    tracks.append(track)
                }
            }

            DispatchQueue.main.async {
                state.tracks = tracks
                state.loadingTracks = false
                if tracks.count == 1 {
                    state.selectedTrack = tracks[0]
                    state.inputInfo = tracks[0].displayName
                } else if tracks.isEmpty {
                    state.inputInfo = "No MIDI tracks found"
                } else {
                    state.inputInfo = "\(tracks.count) MIDI tracks found — select one below"
                }
            }
        }
    }
}


// MARK: - Track Selector

struct TrackSelectorSection: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SectionCard {
            VStack(alignment: .leading, spacing: 10) {
                SectionHeader(title: "Select Track", icon: "pianokeys")

                ForEach(state.tracks) { track in
                    let isSelected = state.selectedTrack?.id == track.id
                    let isPlayingThis = state.playingTrackId == track.id

                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 10) {
                            Image(systemName: isSelected
                                  ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(isSelected ? Theme.accent : Theme.subtleText)
                                .font(.system(size: 18))

                            VStack(alignment: .leading, spacing: 2) {
                                Text(track.name)
                                    .font(.system(size: 13, weight: isSelected ? .semibold : .medium))
                                    .foregroundColor(Theme.bodyText)

                                HStack(spacing: 8) {
                                    Text("\(track.noteCount) notes")
                                    if let dur = track.duration {
                                        Text(String(format: "%.1fs", dur))
                                    }
                                    if let prog = track.program, prog >= 0 {
                                        Text(gmName(program: prog))
                                    }
                                }
                                .font(.system(size: 11))
                                .foregroundColor(Theme.subtleText)
                            }

                            Spacer()

                            if track.midiPath != nil {
                                Button(action: {
                                    if isPlayingThis {
                                        state.stopTrackPlayback()
                                    } else {
                                        state.playTrack(track)
                                    }
                                }) {
                                    Image(systemName: isPlayingThis
                                          ? "stop.circle.fill" : "play.circle.fill")
                                        .font(.system(size: 22))
                                        .foregroundColor(isPlayingThis
                                                         ? .red : Theme.accent)
                                }
                                .buttonStyle(.plain)
                            }
                        }

                        if !track.midiNotes.isEmpty {
                            PianoRollView(
                                notes: track.midiNotes,
                                playbackProgress: isPlayingThis
                                    ? state.trackPlaybackProgress : nil
                            )
                                .frame(height: 50)
                                .background(Color.black.opacity(0.3))
                                .cornerRadius(6)
                        }
                    }
                    .contentShape(Rectangle())
                    .padding(.vertical, 8)
                    .padding(.horizontal, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 10)
                            .fill(isSelected
                                  ? Theme.accent.opacity(0.1)
                                  : Color.white.opacity(0.02))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(isSelected
                                    ? Theme.accent.opacity(0.35)
                                    : Color.white.opacity(0.04),
                                    lineWidth: 1)
                    )
                    .onTapGesture {
                        state.selectedTrack = track
                    }
                }
            }
        }
    }
}


// MARK: - Parameters

struct ParameterSection: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        SectionCard {
            VStack(alignment: .leading, spacing: 12) {
                SectionHeader(title: "Parameters", icon: "slider.horizontal.3")

                LabeledSlider(label: "Notes",
                              value: $state.numEvents,
                              range: 10...300, step: 10,
                              format: "%.0f")

                LabeledSlider(label: "Top-p",
                              value: $state.topP,
                              range: 0.5...1.0, step: 0.01,
                              format: "%.2f")

                LabeledSlider(label: "Temp",
                              value: $state.temperature,
                              range: 0.5...2.0, step: 0.05,
                              format: "%.2f")

                HStack(spacing: 12) {
                    Text("Model")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.bodyText)
                        .frame(width: 70, alignment: .leading)

                    HStack(spacing: 0) {
                        ForEach(["small", "medium", "large", "aria"], id: \.self) { size in
                            let isActive = state.modelSize == size
                            let label = size == "aria" ? "Aria" : size.capitalized
                            Button(action: { state.modelSize = size }) {
                                Text(label)
                                    .font(.system(size: 11, weight: isActive ? .semibold : .regular))
                                    .foregroundColor(isActive ? .white : Theme.subtleText)
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 5)
                                    .background(
                                        RoundedRectangle(cornerRadius: 6)
                                            .fill(isActive
                                                  ? Theme.accent.opacity(0.4)
                                                  : Color.clear)
                                    )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(3)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.white.opacity(0.05))
                    )
                }
            }
        }
    }
}

struct LabeledSlider: View {
    let label: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let step: Double
    let format: String

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 12))
                .foregroundColor(Theme.bodyText)
                .frame(width: 70, alignment: .leading)
            Slider(value: $value, in: range, step: step)
                .tint(Theme.accent)
            Text(String(format: format, value))
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(Theme.accent)
                .frame(width: 44, alignment: .trailing)
        }
    }
}


// MARK: - Generate

struct GenerateSection: View {
    @EnvironmentObject var state: AppState

    var canGenerate: Bool {
        state.inputPath != nil && !state.isRunning
            && (state.tracks.isEmpty || state.selectedTrack != nil)
    }

    var body: some View {
        VStack(spacing: 10) {
            Button(action: generate) {
                HStack(spacing: 8) {
                    if state.isRunning {
                        ProgressView()
                            .controlSize(.small)
                            .scaleEffect(0.7)
                            .tint(.white)
                    } else {
                        Image(systemName: "wand.and.stars")
                            .font(.system(size: 14, weight: .semibold))
                    }
                    Text(state.isRunning ? "Generating..." : "Generate")
                        .font(.system(size: 14, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(canGenerate
                              ? AnyShapeStyle(Theme.accentGradient)
                              : AnyShapeStyle(Color.white.opacity(0.06)))
                )
            }
            .buttonStyle(.plain)
            .disabled(!canGenerate)

            if state.isRunning || state.stage == .done || state.stage == .failed {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        HStack(spacing: 6) {
                            Circle()
                                .fill(state.stage == .failed
                                      ? Color.red
                                      : (state.stage == .done
                                         ? Color.green : Theme.accent))
                                .frame(width: 6, height: 6)
                            Text(state.stage.rawValue)
                                .font(.system(size: 12))
                                .foregroundColor(state.stage == .failed
                                                 ? .red : Theme.bodyText)
                        }
                        Spacer()
                        if !state.progressText.isEmpty {
                            Text(state.progressText)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundColor(Theme.subtleText)
                        }
                    }
                    if state.stage == .failed && !state.errorText.isEmpty {
                        Text(state.errorText)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(.red.opacity(0.8))
                            .lineLimit(3)
                    }
                }
            }
        }
    }

    func generate() {
        guard let inputPath = state.inputPath else { return }
        state.isRunning = true
        state.stage = .extracting
        state.progressText = ""
        state.errorText = ""
        state.resetOutputPlayback()
        state.parsedNotes = []
        state.instrumentTracks = []

        let outputDir = FileManager.default.temporaryDirectory
        let outputFile = outputDir.appendingPathComponent(
            "amt_\(UUID().uuidString.prefix(8)).mid")
        let outputPath = outputFile.path

        DispatchQueue.global().async {
            PipelineRunner.run(
                inputPath: inputPath,
                outputPath: outputPath,
                numEvents: Int(state.numEvents),
                topP: state.topP,
                temperature: state.temperature,
                promptSeconds: nil,
                modelSize: state.modelSize,
                selectedTrack: state.selectedTrack,
                onStage: { stage in
                    DispatchQueue.main.async { state.stage = stage }
                },
                onProgress: { text in
                    DispatchQueue.main.async { state.progressText = text }
                },
                onComplete: { success, errorMsg in
                    DispatchQueue.main.async {
                        state.isRunning = false
                        if success && FileManager.default.fileExists(atPath: outputPath) {
                            state.stage = .done
                            let result = MIDIParser.fullParse(path: outputPath)
                            state.configureOutputPlayback(
                                path: outputPath,
                                notes: result.notes,
                                duration: MIDIParser.duration(path: outputPath)
                            )
                            state.instrumentTracks = MIDIParser.buildInstrumentTracks(from: result)
                        } else {
                            state.stage = .failed
                            state.errorText = errorMsg.isEmpty ? "Pipeline exited with error" : errorMsg
                        }
                    }
                }
            )
        }
    }
}


// MARK: - Pipeline Runner

struct PipelineRunner {
    static func run(
        inputPath: String,
        outputPath: String,
        numEvents: Int,
        topP: Double,
        temperature: Double,
        promptSeconds: Double?,
        modelSize: String,
        selectedTrack: TrackInfo?,
        onStage: @escaping (GenerationStage) -> Void,
        onProgress: @escaping (String) -> Void,
        onComplete: @escaping (Bool, String) -> Void
    ) {
        let isAria = modelSize == "aria"

        var args: [String]
        if isAria {
            let pipeline = PythonEnvironment.script("aria_pipeline.py")
            let ariaDuration = max(10.0, Double(numEvents) / 5.0)
            args = [
                pipeline, inputPath,
                "--output", outputPath,
                "--length", "2048",
                "--max-duration", String(format: "%.0f", ariaDuration),
                "--temp", String(format: "%.2f", temperature),
                "--min-p", "0.035",
            ]
            if let ps = promptSeconds {
                args += ["--prompt-duration", String(format: "%.0f", ps)]
            }
        } else {
            let pipeline = PythonEnvironment.script("amt_pipeline.py")
            args = [
                pipeline, inputPath,
                "--output", outputPath,
                "--no-inject",
                "--num-events", String(numEvents),
                "--top-p", String(format: "%.2f", topP),
                "--temperature", String(format: "%.2f", temperature),
                "--piano-only",
                "--model-size", modelSize,
                "--device", "mps",
            ]
        }
        if !isAria, let track = selectedTrack {
            if track.type == "midi", let idx = track.index {
                args += ["--midi-track", String(idx)]
            } else if track.type == "logicx" {
                if let tid = track.trackId {
                    args += ["--track-id", String(tid)]
                }
                if let sid = track.subId {
                    args += ["--sub-id", sid]
                }
            }
        }

        let proc = PythonEnvironment.process(arguments: args)

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe

        // Collect stdout lines that look like errors
        var capturedError = ""
        let errorLock = NSLock()

        // Parse stdout for stage changes
        stdoutPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty,
                  let line = String(data: data, encoding: .utf8)
            else { return }

            for l in line.components(separatedBy: .newlines) {
                let trimmed = l.trimmingCharacters(in: .whitespaces)
                if trimmed.contains("Stage 1:") || trimmed.contains("Loading MIDI") || trimmed.contains("Input:") {
                    onStage(.extracting)
                } else if trimmed.contains("Stage 2:") || trimmed.contains("AMT token") || trimmed.contains("Extracted MIDI from Logic") {
                    onStage(.converting)
                } else if trimmed.contains("Loading AMT model") || trimmed.contains("Loading Aria model") {
                    onStage(.loadingModel)
                } else if trimmed.contains("Stage 3:") || trimmed.contains("Generating with Aria") || (trimmed.contains("Generating") && trimmed.contains("notes")) {
                    onStage(.generating)
                } else if trimmed.contains("Stage 4:") {
                    onStage(.convertingOutput)
                } else if trimmed.contains("Generated") && trimmed.contains("notes") {
                    onProgress(trimmed)
                } else if trimmed.contains("Output:") && trimmed.contains("notes") {
                    onProgress(trimmed)
                } else if trimmed.contains("Pipeline complete") || trimmed.hasPrefix("Done.") {
                    onStage(.done)
                } else if trimmed.hasPrefix("ERROR:") || trimmed.contains("Traceback") || trimmed.contains("Error:") {
                    errorLock.lock()
                    if capturedError.isEmpty {
                        capturedError = trimmed
                    }
                    errorLock.unlock()
                }
            }
        }

        // Parse stderr for tqdm progress + capture errors
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            guard !data.isEmpty,
                  let text = String(data: data, encoding: .utf8)
            else { return }
            // tqdm uses \r for updates; grab the last segment
            let segments = text.components(separatedBy: "\r")
            if let last = segments.last(where: {
                $0.contains("%") || $0.contains("events")
            }) {
                let trimmed = last.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    onProgress(trimmed)
                }
            }
            // Capture stderr errors (Python tracebacks)
            for segment in text.components(separatedBy: .newlines) {
                let trimmed = segment.trimmingCharacters(in: .whitespacesAndNewlines)
                if trimmed.contains("Error") || trimmed.contains("Exception") || trimmed.contains("Traceback") {
                    errorLock.lock()
                    if capturedError.isEmpty {
                        capturedError = trimmed
                    }
                    errorLock.unlock()
                }
            }
        }

        proc.terminationHandler = { process in
            stdoutPipe.fileHandleForReading.readabilityHandler = nil
            stderrPipe.fileHandleForReading.readabilityHandler = nil
            errorLock.lock()
            let err = capturedError
            errorLock.unlock()
            onComplete(process.terminationStatus == 0, err)
        }

        do {
            try proc.run()
        } catch {
            onComplete(false, error.localizedDescription)
        }
    }
}


// MARK: - Python Environment

/// Finds the Python interpreter and the pipeline scripts.
///
/// `AMT_PYTHON` and `AMT_PYTHON_DIR` override both.  Otherwise the repo's
/// `python/` folder is found by walking up from the executable (or the working
/// directory), and the interpreter is the repo's `.venv`, then `python3` on PATH.
enum PythonEnvironment {
    private static let env = ProcessInfo.processInfo.environment

    static let scriptsDir: String? = {
        if let dir = env["AMT_PYTHON_DIR"] { return dir }
        let fm = FileManager.default
        let starts = [Bundle.main.executablePath, fm.currentDirectoryPath].compactMap { $0 }
        for start in starts {
            var url = URL(fileURLWithPath: start).resolvingSymlinksInPath()
            for _ in 0..<8 {
                let candidate = url.appendingPathComponent("python/amt_pipeline.py")
                if fm.fileExists(atPath: candidate.path) {
                    return url.appendingPathComponent("python").path
                }
                url.deleteLastPathComponent()
            }
        }
        return nil
    }()

    static let python: String = {
        if let py = env["AMT_PYTHON"] { return py }
        if let dir = scriptsDir {
            let venv = URL(fileURLWithPath: dir)
                .deletingLastPathComponent()
                .appendingPathComponent(".venv/bin/python").path
            if FileManager.default.isExecutableFile(atPath: venv) { return venv }
        }
        return "python3"
    }()

    static func script(_ name: String) -> String {
        URL(fileURLWithPath: scriptsDir ?? "python").appendingPathComponent(name).path
    }

    static func process(arguments: [String]) -> Process {
        let proc = Process()
        if python.contains("/") {
            proc.executableURL = URL(fileURLWithPath: python)
            proc.arguments = arguments
        } else {
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            proc.arguments = [python] + arguments
        }
        var procEnv = env
        if let dir = scriptsDir {
            procEnv["PYTHONPATH"] = [dir, env["PYTHONPATH"]].compactMap { $0 }.joined(separator: ":")
        }
        proc.environment = procEnv
        return proc
    }
}


// MARK: - MIDI Parser (lightweight)

struct MIDIParser {
    /// Parse result with per-track program info
    struct ParseResult {
        let notes: [MIDINote]
        // Map of (trackIndex, channel) → program number
        let programs: [Int: Int] // channel → program
    }

    static func parse(path: String) -> [MIDINote] {
        return fullParse(path: path).notes
    }

    static func fullParse(path: String) -> ParseResult {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else {
            return ParseResult(notes: [], programs: [:])
        }
        guard data.count > 14 else {
            return ParseResult(notes: [], programs: [:])
        }

        var notes: [MIDINote] = []
        var programs: [Int: Int] = [:] // channel → program
        let bytes = [UInt8](data)
        var pos = 0

        // Read header
        guard bytes.count > 14,
              bytes[0] == 0x4D, bytes[1] == 0x54,
              bytes[2] == 0x68, bytes[3] == 0x64 // "MThd"
        else { return ParseResult(notes: [], programs: [:]) }

        let _ = (Int(bytes[12]) << 8) | Int(bytes[13]) // ticks per beat
        pos = 14

        var trackNum = 0

        // Read tracks
        while pos + 8 < bytes.count {
            guard bytes[pos] == 0x4D, bytes[pos+1] == 0x54,
                  bytes[pos+2] == 0x72, bytes[pos+3] == 0x6B // "MTrk"
            else { break }

            let trackLen = (Int(bytes[pos+4]) << 24) | (Int(bytes[pos+5]) << 16) |
                           (Int(bytes[pos+6]) << 8) | Int(bytes[pos+7])
            pos += 8
            let trackEnd = min(pos + trackLen, bytes.count)
            let currentTrack = trackNum
            trackNum += 1

            var tick = 0
            var runningStatus: UInt8 = 0
            var channel = 0
            var trackProgram = -1
            var openNotes: [(tick: Int, pitch: Int, velocity: Int, channel: Int)] = []

            while pos < trackEnd {
                // Read variable-length delta
                var delta = 0
                while pos < trackEnd {
                    let b = bytes[pos]; pos += 1
                    delta = (delta << 7) | Int(b & 0x7F)
                    if b & 0x80 == 0 { break }
                }
                tick += delta

                guard pos < trackEnd else { break }
                var status = bytes[pos]

                if status & 0x80 != 0 {
                    pos += 1
                    if status < 0xF0 { runningStatus = status }
                } else {
                    status = runningStatus
                }

                let msgType = status & 0xF0
                channel = Int(status & 0x0F)

                switch msgType {
                case 0x90: // note on
                    guard pos + 1 < trackEnd else { pos = trackEnd; break }
                    let pitch = Int(bytes[pos]); let vel = Int(bytes[pos+1]); pos += 2
                    if vel > 0 {
                        openNotes.append((tick, pitch, vel, channel))
                    } else {
                        if let idx = openNotes.lastIndex(where: { $0.pitch == pitch && $0.channel == channel }) {
                            let on = openNotes.remove(at: idx)
                            notes.append(MIDINote(startTick: on.tick, pitch: on.pitch,
                                                  velocity: on.velocity,
                                                  durationTicks: tick - on.tick,
                                                  channel: on.channel,
                                                  trackIndex: currentTrack,
                                                  program: trackProgram))
                        }
                    }
                case 0x80: // note off
                    guard pos + 1 < trackEnd else { pos = trackEnd; break }
                    let pitch = Int(bytes[pos]); pos += 2
                    if let idx = openNotes.lastIndex(where: { $0.pitch == pitch && $0.channel == channel }) {
                        let on = openNotes.remove(at: idx)
                        notes.append(MIDINote(startTick: on.tick, pitch: on.pitch,
                                              velocity: on.velocity,
                                              durationTicks: tick - on.tick,
                                              channel: on.channel,
                                              trackIndex: currentTrack,
                                              program: trackProgram))
                    }
                case 0xA0, 0xB0, 0xE0: // poly pressure, CC, pitch bend
                    pos += 2
                case 0xC0: // program change
                    guard pos < trackEnd else { pos = trackEnd; break }
                    let prog = Int(bytes[pos]); pos += 1
                    trackProgram = prog
                    programs[channel] = prog
                case 0xD0: // channel pressure
                    pos += 1
                case 0xF0:
                    if status == 0xFF { // meta event
                        guard pos + 1 < trackEnd else { pos = trackEnd; break }
                        pos += 1 // meta type
                        var metaLen = 0
                        while pos < trackEnd {
                            let b = bytes[pos]; pos += 1
                            metaLen = (metaLen << 7) | Int(b & 0x7F)
                            if b & 0x80 == 0 { break }
                        }
                        pos += metaLen
                    } else if status == 0xF0 || status == 0xF7 { // sysex
                        var sysLen = 0
                        while pos < trackEnd {
                            let b = bytes[pos]; pos += 1
                            sysLen = (sysLen << 7) | Int(b & 0x7F)
                            if b & 0x80 == 0 { break }
                        }
                        pos += sysLen
                    }
                default:
                    break
                }
            }
            pos = trackEnd
        }

        return ParseResult(notes: notes, programs: programs)
    }

    static func buildInstrumentTracks(from result: ParseResult) -> [InstrumentTrack] {
        let notes = result.notes
        guard !notes.isEmpty else { return [] }

        // Group notes by (trackIndex, program)
        var grouped: [Int: [MIDINote]] = [:] // trackIndex → notes
        for note in notes {
            grouped[note.trackIndex, default: []].append(note)
        }

        var tracks: [InstrumentTrack] = []
        for trackIdx in grouped.keys.sorted() {
            let trackNotes = grouped[trackIdx]!
            let ch = trackNotes.first?.channel ?? 0
            let prog = trackNotes.first?.program ?? -1
            let isDrums = ch == 9
            let name: String
            if isDrums {
                name = "Drums"
            } else if prog >= 0 {
                let gm = gmName(program: prog)
                name = gm.isEmpty ? "Track \(trackIdx)" : gm
            } else {
                name = "Track \(trackIdx)"
            }
            // Write a temp MIDI file for this instrument
            let tmpPath = NSTemporaryDirectory() + "amt_inst_\(trackIdx)_\(UUID().uuidString.prefix(6)).mid"
            writeMIDI(notes: trackNotes, program: prog >= 0 ? prog : 0,
                      channel: ch, to: tmpPath)

            tracks.append(InstrumentTrack(
                program: prog,
                channel: ch,
                name: name,
                notes: trackNotes,
                midiPath: tmpPath
            ))
        }
        return tracks
    }

    /// Write a minimal Format 0 MIDI file from notes
    static func writeMIDI(notes: [MIDINote], program: Int, channel: Int, to path: String) {
        var bytes: [UInt8] = []

        // Collect events: program_change + note_on/note_off
        struct Event: Comparable {
            let tick: Int
            let sortKey: Int // 0=note_off, 1=program, 2=note_on
            let data: [UInt8]
            static func < (a: Event, b: Event) -> Bool {
                if a.tick != b.tick { return a.tick < b.tick }
                return a.sortKey < b.sortKey
            }
        }

        let ch = UInt8(channel & 0x0F)
        var events: [Event] = []
        events.append(Event(tick: 0, sortKey: 1,
                            data: [0xC0 | ch, UInt8(program & 0x7F)]))

        for note in notes {
            events.append(Event(tick: note.startTick, sortKey: 2,
                                data: [0x90 | ch, UInt8(note.pitch & 0x7F),
                                       UInt8(note.velocity & 0x7F)]))
            events.append(Event(tick: note.startTick + note.durationTicks, sortKey: 0,
                                data: [0x80 | ch, UInt8(note.pitch & 0x7F), 0]))
        }
        events.sort()

        // Build track data
        var trackData: [UInt8] = []
        // Tempo meta: 120 BPM = 500000 uspb
        trackData += [0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1, 0x20]

        var prevTick = 0
        for ev in events {
            let delta = max(0, ev.tick - prevTick)
            trackData += varLen(delta)
            trackData += ev.data
            prevTick = ev.tick
        }
        // End of track
        trackData += [0x00, 0xFF, 0x2F, 0x00]

        // MThd
        let tpb: UInt16 = 480
        bytes += [0x4D, 0x54, 0x68, 0x64] // "MThd"
        bytes += [0x00, 0x00, 0x00, 0x06] // header length
        bytes += [0x00, 0x00]             // format 0
        bytes += [0x00, 0x01]             // 1 track
        bytes += [UInt8(tpb >> 8), UInt8(tpb & 0xFF)]

        // MTrk
        bytes += [0x4D, 0x54, 0x72, 0x6B] // "MTrk"
        let len = UInt32(trackData.count)
        bytes += [UInt8(len >> 24), UInt8((len >> 16) & 0xFF),
                  UInt8((len >> 8) & 0xFF), UInt8(len & 0xFF)]
        bytes += trackData

        try? Data(bytes).write(to: URL(fileURLWithPath: path))
    }

    static func varLen(_ value: Int) -> [UInt8] {
        var v = value
        var result: [UInt8] = [UInt8(v & 0x7F)]
        v >>= 7
        while v > 0 {
            result.insert(UInt8((v & 0x7F) | 0x80), at: 0)
            v >>= 7
        }
        return result
    }

    static func duration(path: String) -> Double {
        // Use mido to get duration (more reliable than manual calc)
        let proc = PythonEnvironment.process(arguments: [
            "-c", "import sys, mido; print(mido.MidiFile(sys.argv[1]).length)", path,
        ])
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()
        try? proc.run()
        proc.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let str = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "0"
        return Double(str) ?? 0
    }
}


// MARK: - Output Section

struct OutputSection: View {
    @EnvironmentObject var state: AppState

    // Hue per instrument for visual differentiation
    static let trackHues: [Double] = [0.6, 0.8, 0.15, 0.0, 0.45, 0.3, 0.55, 0.9]

    var body: some View {
        SectionCard {
            VStack(alignment: .leading, spacing: 12) {
                // Header with full MIDI drag + controls
                HStack {
                    SectionHeader(title: "Output", icon: "waveform")
                    Spacer()
                    Text("\(state.parsedNotes.count) notes | \(String(format: "%.1f", state.outputDuration))s")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(Theme.subtleText)
                }

                // Full combined piano roll — draggable
                PianoRollView(
                    notes: state.parsedNotes,
                    playbackProgress: state.outputPlaybackProgress > 0
                        ? state.outputPlaybackProgress : nil
                )
                    .frame(height: 120)
                    .background(Color.black.opacity(0.4))
                    .cornerRadius(8)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color.white.opacity(0.06), lineWidth: 1)
                    )
                    .onDrag { midiItemProvider() }
                    .help("Drag this into Logic Pro's timeline")

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(formatTime(state.outputCurrentTime))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(Theme.subtleText)
                        Spacer()
                        Text("TIMELINE")
                            .font(.system(size: 10, weight: .semibold, design: .rounded))
                            .foregroundColor(Theme.subtleText)
                            .tracking(1)
                        Spacer()
                        Text(formatTime(state.outputPlayableDuration))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(Theme.subtleText)
                    }

                    Slider(
                        value: Binding(
                            get: { state.outputCurrentTime },
                            set: { state.seekOutput(to: $0) }
                        ),
                        in: 0...max(state.outputPlayableDuration, 0.001)
                    )
                    .tint(Theme.accent)
                    .disabled(state.outputPlayableDuration <= 0)
                }

                // Controls row
                HStack(spacing: 10) {
                    Button(action: state.toggleOutputPlayback) {
                        HStack(spacing: 5) {
                            Image(systemName: state.isPlaying
                                  ? "stop.fill" : "play.fill")
                                .font(.system(size: 10))
                            Text(state.isPlaying ? "Stop" : "Play All")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(state.isPlaying
                                      ? Color.red.opacity(0.6)
                                      : Theme.accent.opacity(0.4))
                        )
                    }
                    .buttonStyle(.plain)

                    Text("Drag piano roll into Logic Pro")
                        .font(.system(size: 10))
                        .foregroundColor(Theme.subtleText)
                        .italic()

                    Spacer()

                    Button(action: saveMIDI) {
                        HStack(spacing: 5) {
                            Image(systemName: "square.and.arrow.down")
                                .font(.system(size: 10))
                            Text("Save MIDI")
                                .font(.system(size: 12, weight: .medium))
                        }
                        .foregroundColor(Theme.bodyText)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 7)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.white.opacity(0.08))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.white.opacity(0.12), lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                }

                // Per-instrument breakdown
                if state.instrumentTracks.count > 1 {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("INSTRUMENTS")
                            .font(.system(size: 10, weight: .semibold, design: .rounded))
                            .foregroundColor(Theme.subtleText)
                            .tracking(1)
                            .padding(.top, 4)

                        ForEach(Array(state.instrumentTracks.enumerated()), id: \.element.id) { idx, track in
                            let hue = Self.trackHues[idx % Self.trackHues.count]
                            let trackColor = Color(hue: hue, saturation: 0.65, brightness: 0.85)
                            let isPlayingThis = state.playingInstrumentId == track.id

                            VStack(alignment: .leading, spacing: 4) {
                                HStack(spacing: 8) {
                                    Circle()
                                        .fill(trackColor)
                                        .frame(width: 8, height: 8)

                                    Text(track.name)
                                        .font(.system(size: 12, weight: .medium))
                                        .foregroundColor(Theme.bodyText)

                                    Text("\(track.notes.count) notes")
                                        .font(.system(size: 10))
                                        .foregroundColor(Theme.subtleText)

                                    if track.channel == 9 {
                                        Text("CH 10")
                                            .font(.system(size: 9, weight: .medium, design: .monospaced))
                                            .foregroundColor(trackColor.opacity(0.8))
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 1)
                                            .background(
                                                RoundedRectangle(cornerRadius: 3)
                                                    .fill(trackColor.opacity(0.15))
                                            )
                                    }

                                    Spacer()

                                    if track.midiPath != nil {
                                        Button(action: {
                                            if isPlayingThis {
                                                state.stopInstrumentPlayback()
                                            } else {
                                                state.playInstrument(track)
                                            }
                                        }) {
                                            Image(systemName: isPlayingThis
                                                  ? "stop.circle.fill" : "play.circle.fill")
                                                .font(.system(size: 18))
                                                .foregroundColor(isPlayingThis
                                                                 ? .red : trackColor)
                                        }
                                        .buttonStyle(.plain)
                                    }
                                }

                                PianoRollView(
                                    notes: track.notes,
                                    noteHue: hue,
                                    playbackProgress: isPlayingThis
                                        ? state.instrumentPlaybackProgress : nil
                                )
                                    .frame(height: 40)
                                    .background(Color.black.opacity(0.3))
                                    .cornerRadius(6)
                                    .onDrag {
                                        guard let mp = track.midiPath else { return NSItemProvider() }
                                        let url = URL(fileURLWithPath: mp)
                                        let provider = NSItemProvider()
                                        provider.registerFileRepresentation(
                                            forTypeIdentifier: UTType.midi.identifier,
                                            fileOptions: [],
                                            visibility: .all
                                        ) { completion in
                                            completion(url, true, nil)
                                            return nil
                                        }
                                        return provider
                                    }
                                    .help("Drag into Logic Pro's timeline")
                            }
                            .padding(.vertical, 6)
                            .padding(.horizontal, 8)
                            .background(
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(Color.white.opacity(0.02))
                            )
                        }
                    }
                }
            }
        }
    }

    func midiItemProvider() -> NSItemProvider {
        guard let path = state.outputPath else { return NSItemProvider() }
        let url = URL(fileURLWithPath: path)
        let provider = NSItemProvider()
        provider.registerFileRepresentation(
            forTypeIdentifier: UTType.midi.identifier,
            fileOptions: [],
            visibility: .all
        ) { completion in
            completion(url, true, nil)
            return nil
        }
        return provider
    }

    func formatTime(_ seconds: Double) -> String {
        let total = max(Int(seconds.rounded(.down)), 0)
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    func saveMIDI() {
        guard let srcPath = state.outputPath else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "mid")!]
        panel.nameFieldStringValue = "generated.mid"
        if panel.runModal() == .OK, let url = panel.url {
            try? FileManager.default.copyItem(
                at: URL(fileURLWithPath: srcPath), to: url)
        }
    }
}


// MARK: - Piano Roll

struct PianoRollView: View {
    let notes: [MIDINote]
    var noteHue: Double? = nil  // fixed hue for single-instrument view
    var playbackProgress: Double? = nil  // 0.0–1.0 playhead position

    var body: some View {
        Canvas { context, size in
            guard !notes.isEmpty else {
                context.draw(
                    Text("No notes")
                        .font(.system(size: 11))
                        .foregroundColor(Color.white.opacity(0.3)),
                    at: CGPoint(x: size.width / 2, y: size.height / 2))
                return
            }

            let minPitch = notes.map(\.pitch).min()!
            let maxPitch = notes.map(\.pitch).max()!
            let minTick = notes.map(\.startTick).min()!
            let maxTick = notes.map { $0.startTick + $0.durationTicks }.max()!
            let tickSpan = max(maxTick - minTick, 1)
            let pitchRange = max(maxPitch - minPitch, 1)

            let margin: CGFloat = 6
            let w = size.width - margin * 2
            let h = size.height - margin * 2
            let noteHeight = max(min(h / CGFloat(pitchRange + 2), 6), 2)

            // Grid lines at C notes
            for p in stride(from: (minPitch / 12) * 12, through: maxPitch, by: 12) {
                let y = margin + h - CGFloat(p - minPitch + 1) / CGFloat(pitchRange + 2) * h
                context.stroke(
                    Path { path in
                        path.move(to: CGPoint(x: margin, y: y))
                        path.addLine(to: CGPoint(x: size.width - margin, y: y))
                    },
                    with: .color(Color.white.opacity(0.06)),
                    lineWidth: 0.5
                )
            }

            // Notes
            for note in notes {
                let x = margin + CGFloat(note.startTick - minTick) / CGFloat(tickSpan) * w
                let noteW = max(CGFloat(note.durationTicks) / CGFloat(tickSpan) * w, 2)
                let y = margin + h - CGFloat(note.pitch - minPitch + 1) / CGFloat(pitchRange + 2) * h

                let alpha = 0.5 + Double(note.velocity) / 127.0 * 0.5
                let hue: Double
                if let fixed = noteHue {
                    hue = fixed
                } else {
                    hue = 0.6 + Double(note.pitch % 12) / 12.0 * 0.15
                }
                let color = Color(hue: hue, saturation: 0.7, brightness: 0.9)
                    .opacity(alpha)

                let rect = CGRect(x: x, y: y - noteHeight / 2,
                                  width: noteW, height: noteHeight)
                context.fill(
                    Path(roundedRect: rect, cornerRadius: 1.5),
                    with: .color(color)
                )
            }

            // Playhead
            if let progress = playbackProgress, progress > 0 {
                let absoluteTick = Double(maxTick) * progress
                let normalizedTick = min(
                    max(absoluteTick - Double(minTick), 0),
                    Double(tickSpan)
                )
                let px = margin + CGFloat(normalizedTick / Double(tickSpan)) * w
                // Glow line
                context.stroke(
                    Path { path in
                        path.move(to: CGPoint(x: px, y: 0))
                        path.addLine(to: CGPoint(x: px, y: size.height))
                    },
                    with: .color(Color.white.opacity(0.15)),
                    lineWidth: 3
                )
                // Main line
                context.stroke(
                    Path { path in
                        path.move(to: CGPoint(x: px, y: 0))
                        path.addLine(to: CGPoint(x: px, y: size.height))
                    },
                    with: .color(Color.white.opacity(0.9)),
                    lineWidth: 1.5
                )
            }
        }
    }
}
