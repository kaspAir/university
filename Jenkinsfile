// Phronesis-Forschungscampus – CI/CD-Pipeline
//
// Vorgesehene Jobs (Pipeline script from SCM, je ein Job pro Branch):
//   university develop      → Prüfung + Tests, kein Deploy
//   university test         → Prüfung + Tests + Deploy test
//   university integration  → Prüfung + Tests + Deploy int
//   university main         → Prüfung + Tests + Deploy prod
//
// Tests laufen gegen ein echtes PostgreSQL 16 (Sidecar-Container im Build-Netz), kein SQLite-Ersatz
// (Paket-CLAUDE.md, docs/S0_VORSCHLAG.md). Die Deploy-Stages sind absichtlich NICHT aktiviert: Zielhost und
// Betriebsform sind noch zu entscheiden (Architektur Kap. 24, S0-Vorschlag Lücke L2).
//
// Voraussetzungen Jenkins:
//   - Plugins: Pipeline, Git, Docker Pipeline, JUnit, Credentials (später SSH Agent)
//   - Docker-Socket im Jenkins-Container (docker-compose.jenkins.yml)

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        REPO_URL = 'https://github.com/kaspAir/university'
    }

    stages {

        stage('Prüfung Repository') {
            steps {
                sh '''
                    set -e
                    echo "Commit: $(git rev-parse --short HEAD)"
                    test -f Jenkinsfile
                    test -f CLAUDE.md
                    test -f 00_Basisdokumente/Phronesis_Forschungsverfassung_V0.2.docx
                    test -f 00_Basisdokumente/Phronesis_Campus_Architektur_V0.1.md
                    test -f 00_Basisdokumente/Phronesis_Campus_V0.1/MANIFEST.sha256.json
                    echo "OK: Grundlagen vorhanden"
                '''
            }
        }

        stage('Tests gegen PostgreSQL') {
            steps {
                script {
                    def net = ("campus-ci-" + env.BUILD_TAG).replaceAll(/[^A-Za-z0-9_.-]/, '-').toLowerCase()
                    sh "docker network create ${net}"
                    try {
                        docker.image('postgres:16').withRun(
                            "--network ${net} --name pg-${net} " +
                            "-e POSTGRES_USER=campus_owner -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=campus_test"
                        ) { pg ->
                            docker.image('python:3.12-slim').inside(
                                "--network ${net} -u root " +
                                "-e CAMPUS_TEST_DATABASE_URL=postgresql+psycopg://campus_owner:ci@pg-${net}:5432/campus_test " +
                                "-e CAMPUS_CAPABILITY_LEVEL=development_mock -e PIP_DISABLE_PIP_VERSION_CHECK=1"
                            ) {
                                sh '''
                                    set -e
                                    python --version
                                    pip install --no-cache-dir -r requirements-dev.txt
                                    # auf die Datenbank warten (max. 60 s)
                                    for i in $(seq 1 30); do
                                        python -c "import os, psycopg; psycopg.connect(os.environ['CAMPUS_TEST_DATABASE_URL'].replace('postgresql+psycopg', 'postgresql')).close()" && break
                                        sleep 2
                                    done
                                    mkdir -p reports
                                    pytest -q --junitxml=reports/junit.xml
                                    ruff check apps packages services tests migrations
                                '''
                            }
                        }
                    } finally {
                        sh "docker network rm ${net} || true"
                    }
                }
            }
            post {
                always {
                    junit allowEmptyResults: false, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Deploy') {
            when {
                anyOf {
                    expression { env.JOB_NAME.contains('test') }
                    expression { env.JOB_NAME.contains('integration') }
                    expression { env.JOB_NAME.contains('main') }
                }
            }
            steps {
                error 'Deploy ist noch nicht eingerichtet: Zielhost und Betriebsform offen (Architektur Kap. 24, S0-Vorschlag L2).'
            }
        }
    }
}
